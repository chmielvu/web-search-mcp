"""SQLite-backed page content cache.

Separate .sqlite file (default duckdb_data/cache/page_cache.sqlite).
Uses URL hash lookup (sha256[:32]), TTL expiry, metadata as JSON
roundtrip in metadata_json column, and thread-safe SQLite WAL mode connection.

This module provides the low-level backend. The public PageCache
facade + singleton + observability remain in page_cache.py for
drop-in compatibility with server.py and get_content.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..settings import settings

logger = logging.getLogger(__name__)

PAGE_CACHE_DEFAULT_TTL_SECONDS = int(os.environ.get("PAGE_CACHE_TTL_SECONDS", "604800"))


class PageSQLiteCache:
    """SQLite implementation for the page cache using WAL mode."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()

    def _resolve_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        return Path(settings.page_cache_sqlite_path)

    def _get_connection(self) -> sqlite3.Connection:
        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path), timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA busy_timeout=5000;")
        return con

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
        with con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS page_cache (
                    url_hash TEXT PRIMARY KEY,
                    url_canonical TEXT NOT NULL,
                    page_content TEXT NOT NULL,
                    extraction_method TEXT NOT NULL,
                    word_count INTEGER NOT NULL DEFAULT 0,
                    char_count INTEGER NOT NULL DEFAULT 0,
                    domain TEXT NOT NULL,
                    status_code INTEGER DEFAULT 200,
                    cached_at TEXT NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    metadata_json TEXT
                );
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_page_domain ON page_cache(domain);")
            con.execute("CREATE INDEX IF NOT EXISTS idx_page_cached_at ON page_cache(cached_at);")

    def ensure_store_schema(self, con: sqlite3.Connection) -> None:
        """Isolated schema check for write paths, logging non-fatal warnings."""
        try:
            self._ensure_schema(con)
        except Exception as exc:  # noqa: BLE001
            logger.warning("page_cache: skipped schema initialization/migration: %s", exc)

    def entry_count(self) -> int:
        """Return the number of cached page entries (including expired)."""
        with self._lock:
            try:
                con = self._get_connection()
                try:
                    self._ensure_schema(con)
                    row = con.execute("SELECT COUNT(*) FROM page_cache").fetchone()
                    return int(row[0]) if row else 0
                finally:
                    con.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("page_cache: entry_count failed: %s", exc)
                return 0

    def _compute_url_hash(self, canonical_url: str) -> str:
        """Compute a deterministic hash for a canonical URL."""
        return hashlib.sha256(canonical_url.strip().lower().encode()).hexdigest()[:32]

    def lookup(self, canonical_url: str) -> dict[str, Any] | None:
        """URL-hash lookup with TTL check. Returns None on miss or expiry."""
        return self._lookup_sync(canonical_url)

    async def alookup(self, canonical_url: str) -> dict[str, Any] | None:
        """Async wrapper for :meth:`lookup` that runs SQLite I/O in a thread."""
        return await asyncio.to_thread(self._lookup_sync, canonical_url)

    def _lookup_sync(self, canonical_url: str) -> dict[str, Any] | None:
        url_hash = self._compute_url_hash(canonical_url)
        with self._lock:
            try:
                con = self._get_connection()
                try:
                    self._ensure_schema(con)
                    row = con.execute(
                        """
                        SELECT url_hash, url_canonical, page_content, extraction_method,
                               word_count, char_count, domain, status_code, cached_at,
                               ttl_seconds, metadata_json
                        FROM page_cache
                        WHERE url_hash = ?
                        """,
                        (url_hash,),
                    ).fetchone()

                    if not row:
                        return None

                    # Check TTL
                    cached_at_str = row["cached_at"]
                    ttl_seconds = row["ttl_seconds"]
                    try:
                        cached_at = datetime.fromisoformat(cached_at_str)
                    except ValueError:
                        return None

                    age_seconds = (datetime.now(UTC) - cached_at).total_seconds()
                    if age_seconds > ttl_seconds:
                        logger.debug(
                            "Page cache TTL expired for %s (age=%.1fs, ttl=%ds)",
                            canonical_url,
                            age_seconds,
                            ttl_seconds,
                        )
                        return None

                    # Deserialize metadata
                    metadata: dict[str, Any] = {}
                    if row["metadata_json"]:
                        try:
                            metadata = json.loads(row["metadata_json"])
                        except json.JSONDecodeError:
                            metadata = {}

                    return {
                        "url_hash": row["url_hash"],
                        "url_canonical": row["url_canonical"],
                        "page_content": row["page_content"],
                        "extraction_method": row["extraction_method"],
                        "word_count": row["word_count"],
                        "char_count": row["char_count"],
                        "domain": row["domain"],
                        "status_code": row["status_code"],
                        "cached_at": row["cached_at"],
                        "ttl_seconds": row["ttl_seconds"],
                        "age_seconds": age_seconds,
                        "metadata": metadata,
                    }
                finally:
                    con.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("page_cache lookup failed for %s: %s", canonical_url, exc)
                return None

    def store(
        self,
        canonical_url: str,
        page_content: str,
        extraction_method: str,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Store page content with metadata as JSON roundtrip."""
        actual_ttl = ttl_seconds if ttl_seconds is not None else PAGE_CACHE_DEFAULT_TTL_SECONDS
        self._store_sync(canonical_url, page_content, extraction_method, metadata, actual_ttl)

    async def astore(
        self,
        canonical_url: str,
        page_content: str,
        extraction_method: str,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Async wrapper for :meth:`store` that runs SQLite I/O in a thread."""
        actual_ttl = ttl_seconds if ttl_seconds is not None else PAGE_CACHE_DEFAULT_TTL_SECONDS
        await asyncio.to_thread(
            self._store_sync, canonical_url, page_content, extraction_method, metadata, actual_ttl
        )

    def _store_sync(
        self,
        canonical_url: str,
        page_content: str,
        extraction_method: str,
        metadata: dict[str, Any] | None,
        ttl_seconds: int,
    ) -> None:
        url_hash = self._compute_url_hash(canonical_url)
        now_iso = datetime.now(UTC).isoformat()
        metadata_dict = metadata or {}
        metadata_json = json.dumps(metadata_dict)

        # Extract domain from canonical_url or metadata
        domain = metadata_dict.get("domain", "")
        if not domain:
            try:
                from urllib.parse import urlparse

                domain = urlparse(canonical_url).netloc
            except Exception:  # noqa: BLE001
                domain = ""

        word_count = len(page_content.split())
        char_count = len(page_content)
        status_code = metadata_dict.get("status_code", 200)

        with self._lock:
            try:
                con = self._get_connection()
                try:
                    self.ensure_store_schema(con)
                    with con:
                        con.execute(
                            """
                            INSERT INTO page_cache (
                                url_hash, url_canonical, page_content, extraction_method,
                                word_count, char_count, domain, status_code, cached_at,
                                ttl_seconds, metadata_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(url_hash) DO UPDATE SET
                                url_canonical = excluded.url_canonical,
                                page_content = excluded.page_content,
                                extraction_method = excluded.extraction_method,
                                word_count = excluded.word_count,
                                char_count = excluded.char_count,
                                domain = excluded.domain,
                                status_code = excluded.status_code,
                                cached_at = excluded.cached_at,
                                ttl_seconds = excluded.ttl_seconds,
                                metadata_json = excluded.metadata_json
                            """,
                            (
                                url_hash,
                                canonical_url,
                                page_content,
                                extraction_method,
                                word_count,
                                char_count,
                                domain,
                                status_code,
                                now_iso,
                                ttl_seconds,
                                metadata_json,
                            ),
                        )
                finally:
                    con.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("page_cache store failed for %s: %s", canonical_url, exc)
                raise
