"""Page content cache with observability facade.

Caches extracted markdown content by canonical URL,
with metadata about extraction method and timestamps.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..telemetry import record_cache_lookup
from .observability import emit_cache_lookup_event, emit_cache_store_event
from .page_duckdb import (
    PAGE_CACHE_DEFAULT_TTL_SECONDS,
    PageDuckDBCache as _PageDuckDBCache,
)

logger = logging.getLogger(__name__)

# Re-export for callers that expect the const at this module (e.g. __init__.py)
# (definition lives in page_duckdb.py to be co-located with the impl)


class PageCache:
    """DuckDB-backed page content cache.

    Thin facade over PageDuckDBCache (in page_duckdb.py) that preserves the
    exact public API + singleton used by server.py / get_content.

    Separate DuckDB file controlled by PAGE_CACHE_DUCKDB_PATH.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._backend = _PageDuckDBCache(db_path=db_path)

    def lookup(
        self,
        canonical_url: str,
    ) -> dict[str, Any] | None:
        """Look up cached page content for a URL.

        Delegates to DuckDB backend (URL hash + TTL + metadata JSON).
        Emits via cache observability helpers and records telemetry.
        """
        try:
            return self._lookup_inner(canonical_url)
        except Exception as exc:
            logger.warning("Page cache lookup failed for %s: %s", canonical_url, exc)
            emit_cache_lookup_event(
                logger,
                "page",
                "miss",
                duration_ms=0,
                canonical_url=canonical_url,
                error_type=type(exc).__name__,
            )
            return None

    async def alookup(
        self,
        canonical_url: str,
    ) -> dict[str, Any] | None:
        """Async variant of :meth:`lookup` that runs DuckDB I/O in a thread."""
        # Defensive: if a MagicMock test fixture leaked into production,
        # `_backend.alookup` is auto-created as a sync attribute on `MagicMock()`,
        # so `hasattr` returns True but the value is not awaitable. A real backend
        # exposes `alookup` as a coroutine function (`async def`).
        backend_alookup = getattr(self._backend, "alookup", None)
        if not asyncio.iscoroutinefunction(backend_alookup):
            logger.error(
                "PageCache backend alookup is not a coroutine function -- mock leaked: %s",
                type(self._backend).__name__,
            )
            emit_cache_lookup_event(
                logger,
                "page",
                "miss",
                duration_ms=0,
                canonical_url=canonical_url,
                error_type="invalid_backend",
            )
            return None
        start_time = time.time()
        try:
            result = await self._backend.alookup(canonical_url)
            duration_ms = (time.time() - start_time) * 1000
            formatted = self._format_lookup_result(result, canonical_url, duration_ms / 1000)
            return formatted
        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000
            logger.warning("Page cache lookup failed for %s: %s", canonical_url, exc)
            emit_cache_lookup_event(
                logger,
                "page",
                "miss",
                duration_ms=duration_ms,
                canonical_url=canonical_url,
                error_type=type(exc).__name__,
            )
            return None

    def _lookup_inner(
        self,
        canonical_url: str,
    ) -> dict[str, Any] | None:
        """Synchronous lookup + observability."""
        start_time = time.time()
        result = self._backend.lookup(canonical_url)
        duration = time.time() - start_time
        return self._format_lookup_result(result, canonical_url, duration)

    def _format_lookup_result(
        self,
        result: dict[str, Any] | None,
        canonical_url: str,
        duration: float = 0.0,
    ) -> dict[str, Any] | None:
        """Format backend lookup result and emit observability events."""
        if result is None:
            record_cache_lookup(cache_type="page", hit=False, duration_seconds=duration)
            emit_cache_lookup_event(
                logger,
                "page",
                "miss",
                duration_ms=round(duration * 1000, 3),
                canonical_url=canonical_url,
            )
            return None

        record_cache_lookup(cache_type="page", hit=True, duration_seconds=duration)
        emit_cache_lookup_event(
            logger,
            "page",
            "hit",
            duration_ms=round(duration * 1000, 3),
            age_seconds=round(result.get("age_seconds", 0), 3),
            word_count=result.get("word_count", 0),
            extraction_method=result.get("extraction_method", "unknown"),
            canonical_url=canonical_url,
        )

        # Ensure callers see same keys as before (metadata already parsed by backend)
        return {
            "page_content": result["page_content"],
            "extraction_method": result.get("extraction_method", "unknown"),
            "word_count": result.get("word_count", 0),
            "age_seconds": result.get("age_seconds", 0),
            "cached_at": result.get("cached_at"),
            **({"metadata": result["metadata"]} if "metadata" in result else {}),
        }

    def store(
        self,
        canonical_url: str,
        page_content: str,
        extraction_method: str,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Store resolved page content via DuckDB backend."""
        self._store_inner(canonical_url, page_content, extraction_method, metadata, ttl_seconds)

    async def astore(
        self,
        canonical_url: str,
        page_content: str,
        extraction_method: str,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Async variant of :meth:`store` that runs DuckDB I/O in a thread."""
        try:
            await self._backend.astore(
                canonical_url=canonical_url,
                page_content=page_content,
                extraction_method=extraction_method,
                metadata=metadata,
                ttl_seconds=ttl_seconds,
            )
            logger.debug(
                "Stored page cache entry (url=%s, method=%s, ttl=%s)",
                str(canonical_url)[:50],
                extraction_method,
                ttl_seconds,
            )
            emit_cache_store_event(
                logger,
                "page",
                "ok",
                ttl_seconds=ttl_seconds or PAGE_CACHE_DEFAULT_TTL_SECONDS,
                metadata_present=bool(metadata),
                extraction_method=extraction_method,
                canonical_url=canonical_url,
            )
        except Exception as exc:
            logger.warning("Failed to store page cache entry: %s", exc)
            emit_cache_store_event(
                logger,
                "page",
                "error",
                error_type=type(exc).__name__,
                canonical_url=canonical_url,
            )

    def _store_inner(
        self,
        canonical_url: str,
        page_content: str,
        extraction_method: str,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Synchronous store + observability."""
        try:
            self._backend.store(
                canonical_url=canonical_url,
                page_content=page_content,
                extraction_method=extraction_method,
                metadata=metadata,
                ttl_seconds=ttl_seconds,
            )
            logger.debug(
                "Stored page cache entry (url=%s, method=%s, ttl=%s)",
                str(canonical_url)[:50],
                extraction_method,
                ttl_seconds,
            )
            emit_cache_store_event(
                logger,
                "page",
                "ok",
                ttl_seconds=ttl_seconds or PAGE_CACHE_DEFAULT_TTL_SECONDS,
                metadata_present=bool(metadata),
                extraction_method=extraction_method,
                canonical_url=canonical_url,
            )
        except Exception as exc:
            logger.warning("Failed to store page cache entry: %s", exc)
            emit_cache_store_event(
                logger,
                "page",
                "error",
                error_type=type(exc).__name__,
                canonical_url=canonical_url,
            )


# Singleton instance (lazy init)
_PAGE_CACHE: PageCache | None = None


def get_page_cache(db_path: str | None = None) -> PageCache:
    """Get or create the page cache singleton.

    Uses PAGE_CACHE_DUCKDB_PATH (separate file) unless db_path explicitly passed.
    """
    global _PAGE_CACHE
    if _PAGE_CACHE is None:
        from ..settings import settings

        actual_path = db_path or settings.page_cache_duckdb_path
        _PAGE_CACHE = PageCache(db_path=actual_path)
        logger.info("Initialized page cache at %s", actual_path)
    return _PAGE_CACHE
