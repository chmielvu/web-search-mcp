"""DuckDB-backed page content cache (Phase 5.2).

Separate .duckdb file (default duckdb_data/cache/page_cache.duckdb),
not sharing the analytics DB. Uses URL hash lookup (sha256[:32]),
TTL expiry, metadata as JSON roundtrip in metadata_json column,
and a threading.Lock around all DB operations for safe writes
(and reads) given DuckDB's single-writer model.

This module provides the low-level backend. The public PageCache
facade + singleton + observability remain in page_cache.py for
drop-in compatibility with server.py and get_content.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb


PAGE_CACHE_DEFAULT_TTL_SECONDS = int(os.environ.get("PAGE_CACHE_TTL_SECONDS", "604800"))


class PageDuckDBCache:
    """DuckDB implementation for the page cache.

    Supports:
    - deterministic URL hashing for exact lookup
    - TTL based on created_at + ttl_seconds (no auto-delete on read-expire)
    - metadata dict <-> JSON string roundtrip
    - locked writes (lock held for entire connect+op)
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()

    def _resolve_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        # Lazy import to avoid circulars at module load
        from ..settings import settings

        return Path(settings.page_cache_duckdb_path)

    def _ensure_schema(self, con: duckdb.DuckDBPyConnection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS page_cache (
                id VARCHAR,
                url_canonical VARCHAR,
                url_hash VARCHAR,
                page_content VARCHAR,
                extraction_method VARCHAR,
                word_count BIGINT,
                created_at VARCHAR,
                ttl_seconds BIGINT,
                metadata_json VARCHAR
            )
            """
        )
        # Best-effort index for hash lookups
        try:
            con.execute("CREATE INDEX IF NOT EXISTS idx_page_url_hash ON page_cache(url_hash)")
        except Exception:
            # ignore if concurrent create or older duckdb variant
            pass

    def _compute_url_hash(self, canonical_url: str) -> str:
        """Compute a deterministic hash for a canonical URL (matches prior Lance impl)."""
        return hashlib.sha256(canonical_url.strip().lower().encode()).hexdigest()[:32]

    def lookup(self, canonical_url: str) -> dict[str, Any] | None:
        """URL-hash lookup with TTL check. Returns None on miss or expiry."""
        url_hash = self._compute_url_hash(canonical_url)
        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            con = duckdb.connect(str(path))
            try:
                self._ensure_schema(con)
                row = con.execute(
                    """
                    SELECT
                        page_content,
                        extraction_method,
                        word_count,
                        created_at,
                        ttl_seconds,
                        metadata_json
                    FROM page_cache
                    WHERE url_hash = ?
                    LIMIT 1
                    """,
                    [url_hash],
                ).fetchone()

                if not row:
                    return None

                (
                    page_content,
                    extraction_method,
                    word_count,
                    created_at_str,
                    ttl_seconds,
                    metadata_json,
                ) = row

                if not created_at_str:
                    return None

                try:
                    created_at = datetime.fromisoformat(created_at_str)
                except Exception:
                    return None

                age_seconds = (datetime.now(UTC) - created_at).total_seconds()
                eff_ttl = (
                    int(ttl_seconds) if ttl_seconds is not None else PAGE_CACHE_DEFAULT_TTL_SECONDS
                )

                if age_seconds > eff_ttl:
                    # Expired: return None (miss). Keep row (simple; no delete on lookup).
                    return None

                result: dict[str, Any] = {
                    "page_content": page_content or "",
                    "extraction_method": extraction_method or "unknown",
                    "word_count": int(word_count or 0),
                    "age_seconds": age_seconds,
                    "cached_at": created_at_str,
                }

                if metadata_json:
                    try:
                        parsed = json.loads(metadata_json)
                        if isinstance(parsed, dict):
                            result["metadata"] = parsed
                    except (json.JSONDecodeError, TypeError):
                        pass

                return result
            finally:
                con.close()

    def store(
        self,
        canonical_url: str,
        page_content: str,
        extraction_method: str,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Store with JSON metadata roundtrip. Locked write."""
        url_hash = self._compute_url_hash(canonical_url)

        if ttl_seconds is None:
            ttl_seconds = PAGE_CACHE_DEFAULT_TTL_SECONDS

        word_count = len((page_content or "").split())

        entry_id = uuid.uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        meta_json = json.dumps(metadata) if metadata else ""

        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            con = duckdb.connect(str(path))
            try:
                self._ensure_schema(con)
                # Upsert semantics: remove any existing rows for this URL hash so
                # repeated stores of the same URL don't accumulate duplicate rows
                # (unbounded disk growth). DuckDB lacks INSERT OR REPLACE without a
                # unique constraint, so delete-then-insert under the same lock.
                con.execute(
                    "DELETE FROM page_cache WHERE url_hash = ?",
                    [url_hash],
                )
                con.execute(
                    """
                    INSERT INTO page_cache (
                        id,
                        url_canonical,
                        url_hash,
                        page_content,
                        extraction_method,
                        word_count,
                        created_at,
                        ttl_seconds,
                        metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        entry_id,
                        canonical_url,
                        url_hash,
                        page_content or "",
                        extraction_method or "unknown",
                        word_count,
                        created_at,
                        ttl_seconds,
                        meta_json,
                    ],
                )
            finally:
                con.close()
