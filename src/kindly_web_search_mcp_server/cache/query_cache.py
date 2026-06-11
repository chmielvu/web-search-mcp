"""Exact query cache with observability facade."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from ..telemetry import record_cache_lookup
from .exact_lru import ExactLRUCache, compute_cache_key
from .observability import emit_cache_lookup_event, emit_cache_store_event

logger = logging.getLogger(__name__)

QUERY_CACHE_DEFAULT_TTL_SECONDS = int(
    os.environ.get("QUERY_CACHE_TTL_SECONDS", "86400")
)
QUERY_CACHE_DEFAULT_MAX_ENTRIES = int(
    os.environ.get("QUERY_CACHE_MAX_ENTRIES", "1024")
)


def provider_cache_key(providers: list[str] | None) -> str:
    """Normalize the caller-specified provider set for cache identity."""
    if not providers:
        return "default"
    normalized = sorted(
        {provider.strip().lower() for provider in providers if provider.strip()}
    )
    return ",".join(normalized) if normalized else "default"


def _compute_cache_key(
    normalized_query: str,
    num_results: int,
    rewrite_enabled: bool,
    search_mode: str,
    providers_key: str = "default",
) -> str:
    """Compute a deterministic cache key from search parameters."""
    return compute_cache_key(
        normalized_query, num_results, rewrite_enabled, search_mode, providers_key
    )


class ExactQueryCache:
    """In-memory exact query cache with the existing server-facing API."""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        max_entries: int = QUERY_CACHE_DEFAULT_MAX_ENTRIES,
        default_ttl_seconds: int = QUERY_CACHE_DEFAULT_TTL_SECONDS,
    ) -> None:
        self.db_path = db_path
        self.default_ttl_seconds = default_ttl_seconds
        self._cache = ExactLRUCache(
            max_entries=max_entries,
            default_ttl_seconds=default_ttl_seconds,
        )

    def lookup(
        self,
        normalized_query: str,
        num_results: int,
        rewrite_enabled: bool,
        search_mode: str = "balanced",
        providers_key: str = "default",
    ) -> dict[str, Any] | None:
        """Look up an exact cache hit for the given parameters."""
        start_time = time.time()
        response = self._cache.lookup(
            normalized_query,
            num_results,
            rewrite_enabled,
            search_mode,
            providers_key,
        )
        duration = time.time() - start_time

        if response is None:
            record_cache_lookup(
                cache_type="exact", hit=False, duration_seconds=duration
            )
            emit_cache_lookup_event(
                logger,
                "exact",
                "miss",
                duration_ms=round(duration * 1000, 3),
                normalized_query=normalized_query,
                num_results=num_results,
                rewrite_enabled=rewrite_enabled,
                search_mode=search_mode,
                providers_key=providers_key,
            )
            return None

        record_cache_lookup(cache_type="exact", hit=True, duration_seconds=duration)
        emit_cache_lookup_event(
            logger,
            "exact",
            "hit",
            duration_ms=round(duration * 1000, 3),
            response_size=len(json.dumps(response)),
            normalized_query=normalized_query,
            num_results=num_results,
            rewrite_enabled=rewrite_enabled,
            search_mode=search_mode,
            providers_key=providers_key,
        )
        return response

    def store(
        self,
        normalized_query: str,
        num_results: int,
        rewrite_enabled: bool,
        response: dict[str, Any],
        search_mode: str = "balanced",
        providers_key: str = "default",
        ttl_seconds: int | None = None,
    ) -> None:
        """Store a search response in the exact query cache."""
        response_json = json.dumps(response)
        self._cache.store(
            normalized_query,
            num_results,
            rewrite_enabled,
            search_mode,
            providers_key,
            response,
            ttl_seconds,
        )
        emit_cache_store_event(
            logger,
            "exact",
            "ok",
            ttl_seconds=self.default_ttl_seconds if ttl_seconds is None else ttl_seconds,
            response_size=len(response_json),
            normalized_query=normalized_query,
            num_results=num_results,
            rewrite_enabled=rewrite_enabled,
            search_mode=search_mode,
            providers_key=providers_key,
        )


_QUERY_CACHE: ExactQueryCache | None = None


def get_query_cache(db_path: str | None = None) -> ExactQueryCache:
    """Get or create the exact query cache singleton."""
    global _QUERY_CACHE
    if _QUERY_CACHE is None:
        _QUERY_CACHE = ExactQueryCache(db_path=db_path)
        logger.info(
            "Initialized in-memory exact query LRU cache with max_entries=%s",
            QUERY_CACHE_DEFAULT_MAX_ENTRIES,
        )
    return _QUERY_CACHE
