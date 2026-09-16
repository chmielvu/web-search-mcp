"""Diskcache-backed result cache with TTL."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from diskcache import Cache

from hsearch.config import cache_dir, cache_ttl


def _make_key(provider: str, query: str, params: dict[str, Any]) -> str:
    payload = json.dumps(
        {"p": provider, "q": query, "x": params}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ResultCache:
    """Thin wrapper around diskcache with JSON-serialized values."""

    def __init__(self) -> None:
        try:
            self._path = cache_dir()
            self._cache = Cache(str(self._path))
        except Exception:
            self._path = Path(tempfile.gettempdir()) / "hsearch-cache"
            self._path.mkdir(parents=True, exist_ok=True)
            self._cache = Cache(str(self._path))

    def get(self, provider: str, query: str, params: dict[str, Any]) -> Any | None:
        key = _make_key(provider, query, params)
        return self._cache.get(key)

    def set(
        self,
        provider: str,
        query: str,
        params: dict[str, Any],
        value: Any,
        ttl: int | None = None,
    ) -> None:
        key = _make_key(provider, query, params)
        self._cache.set(key, value, expire=ttl if ttl is not None else cache_ttl())

    def clear(self) -> int:
        n = len(self._cache)
        self._cache.clear()
        return n

    def stats(self) -> dict[str, Any]:
        return {
            "path": str(self._path),
            "entries": len(self._cache),
            "size_bytes": self._cache.volume(),
        }

    def close(self) -> None:
        self._cache.close()
