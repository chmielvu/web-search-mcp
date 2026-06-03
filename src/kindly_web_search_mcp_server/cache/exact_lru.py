"""In-memory exact query LRU cache."""

from __future__ import annotations

import copy
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable


def compute_cache_key(
    normalized_query: str,
    num_results: int,
    rewrite_enabled: bool,
    search_mode: str,
    providers_key: str = "default",
) -> str:
    """Compute a deterministic cache key from search parameters."""
    key_parts = [
        normalized_query.strip().lower(),
        str(num_results),
        str(rewrite_enabled),
        search_mode.strip().lower(),
        providers_key.strip().lower(),
    ]
    composite = "|".join(key_parts)
    return hashlib.sha256(composite.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class ExactLRUEntry:
    response: dict[str, Any]
    created_at: float
    ttl_seconds: int


class ExactLRUCache:
    """Small in-memory LRU cache for exact query responses."""

    def __init__(
        self,
        *,
        max_entries: int,
        default_ttl_seconds: int,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        if default_ttl_seconds < 1:
            raise ValueError("default_ttl_seconds must be at least 1")

        self.max_entries = max_entries
        self.default_ttl_seconds = default_ttl_seconds
        self._time_fn = time_fn or time.time
        self._entries: OrderedDict[str, ExactLRUEntry] = OrderedDict()

    def lookup(
        self,
        normalized_query: str,
        num_results: int,
        rewrite_enabled: bool,
        search_mode: str = "balanced",
        providers_key: str = "default",
    ) -> dict[str, Any] | None:
        key = compute_cache_key(
            normalized_query, num_results, rewrite_enabled, search_mode, providers_key
        )
        entry = self._entries.get(key)
        if entry is None:
            return None

        if self._time_fn() - entry.created_at > entry.ttl_seconds:
            del self._entries[key]
            return None

        self._entries.move_to_end(key)
        return copy.deepcopy(entry.response)

    def store(
        self,
        normalized_query: str,
        num_results: int,
        rewrite_enabled: bool,
        search_mode: str,
        providers_key: str,
        response: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> None:
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl < 1:
            raise ValueError("ttl_seconds must be at least 1")

        key = compute_cache_key(
            normalized_query, num_results, rewrite_enabled, search_mode, providers_key
        )
        self._entries[key] = ExactLRUEntry(
            response=copy.deepcopy(response),
            created_at=self._time_fn(),
            ttl_seconds=ttl,
        )
        self._entries.move_to_end(key)

        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def entry_count(self) -> int:
        return len(self._entries)
