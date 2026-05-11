"""Page content cache for resolved URL content.

Caches extracted markdown content by canonical URL,
with metadata about extraction method and timestamps.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import lancedb

logger = logging.getLogger(__name__)

# Default TTL for page cache (7 days - content changes less frequently)
PAGE_CACHE_DEFAULT_TTL_SECONDS = int(
    os.environ.get("KINDLY_PAGE_CACHE_TTL_SECONDS", "604800")  # 7 days
)

PAGE_CACHE_SCHEMA = [
    ("id", "string"),
    ("url_canonical", "string"),
    ("url_hash", "string"),
    ("page_content", "string"),
    ("extraction_method", "string"),
    ("word_count", "int64"),
    ("created_at", "string"),
    ("ttl_seconds", "int64"),
    ("metadata_json", "string"),  # Optional metadata
]


class PageCache:
    """LanceDB-backed page content cache.

    Caches resolved page content by canonical URL to avoid
    repeated fetching and extraction of the same URLs.
    """

    def __init__(self, db_path: str = "./lancedb_data") -> None:
        self.db_path = db_path
        self._db: lancedb.db.DBConnection | None = None
        self._table: lancedb.table.Table | None = None

    def _get_db(self) -> lancedb.db.DBConnection:
        if self._db is None:
            self._db = lancedb.connect(self.db_path)
        return self._db

    def _get_table(self) -> lancedb.table.Table:
        if self._table is None:
            db = self._get_db()
            try:
                self._table = db.open_table("page_cache")
                logger.debug("Opened existing page_cache table")
            except Exception:
                import pyarrow as pa
                arrow_schema = pa.schema([
                    pa.field("id", pa.string()),
                    pa.field("url_canonical", pa.string()),
                    pa.field("url_hash", pa.string()),
                    pa.field("page_content", pa.string()),
                    pa.field("extraction_method", pa.string()),
                    pa.field("word_count", pa.int64()),
                    pa.field("created_at", pa.string()),
                    pa.field("ttl_seconds", pa.int64()),
                    pa.field("metadata_json", pa.string()),
                ])
                self._table = db.create_table("page_cache", schema=arrow_schema)
                logger.info("Created new page_cache table")
        return self._table

    def _compute_url_hash(self, canonical_url: str) -> str:
        """Compute a deterministic hash for a canonical URL."""
        return hashlib.sha256(canonical_url.strip().lower().encode()).hexdigest()[:32]

    def lookup(
        self,
        canonical_url: str,
    ) -> dict[str, Any] | None:
        """Look up cached page content for a URL.

        Args:
            canonical_url: Canonical URL to look up.

        Returns:
            Dict with page_content, extraction_method, word_count, age_seconds
            if found and not expired. None otherwise.
        """
        url_hash = self._compute_url_hash(canonical_url)
        table = self._get_table()

        try:
            results = (
                table.search()
                .where(f"url_hash = '{url_hash}'")
                .limit(1)
                .to_list()
            )
        except Exception as exc:
            logger.warning("Page cache lookup failed: %s", exc)
            return None

        if not results:
            logger.debug("No page cache hit for URL hash: %s", url_hash[:16])
            return None

        row = results[0]

        # Check TTL
        created_at = datetime.fromisoformat(row["created_at"])
        age_seconds = (datetime.now(UTC) - created_at).total_seconds()
        ttl_seconds = row.get("ttl_seconds", PAGE_CACHE_DEFAULT_TTL_SECONDS)

        if age_seconds > ttl_seconds:
            logger.debug(
                "Page cache expired (age=%.0fs > ttl=%ds) for %s",
                age_seconds,
                ttl_seconds,
                canonical_url[:50],
            )
            return None

        logger.debug(
            "Page cache hit (url=%s, method=%s, age=%.0fs, words=%d)",
            canonical_url[:50],
            row.get("extraction_method", "unknown"),
            age_seconds,
            row.get("word_count", 0),
        )

        result = {
            "page_content": row["page_content"],
            "extraction_method": row.get("extraction_method", "unknown"),
            "word_count": row.get("word_count", 0),
            "age_seconds": age_seconds,
            "cached_at": row["created_at"],
        }

        # Parse optional metadata
        metadata_json = row.get("metadata_json", "")
        if metadata_json:
            try:
                result["metadata"] = json.loads(metadata_json)
            except json.JSONDecodeError:
                pass

        return result

    def store(
        self,
        canonical_url: str,
        page_content: str,
        extraction_method: str,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Store resolved page content in the cache.

        Args:
            canonical_url: Canonical URL for the content.
            page_content: Extracted markdown content.
            extraction_method: Method used (e.g., "http_extract", "nodriver").
            metadata: Optional metadata dict to store.
            ttl_seconds: TTL override. Uses default if None.
        """
        url_hash = self._compute_url_hash(canonical_url)

        if ttl_seconds is None:
            ttl_seconds = PAGE_CACHE_DEFAULT_TTL_SECONDS

        # Compute word count for diagnostics
        word_count = len(page_content.split())

        entry = {
            "id": uuid.uuid4().hex,
            "url_canonical": canonical_url,
            "url_hash": url_hash,
            "page_content": page_content,
            "extraction_method": extraction_method,
            "word_count": word_count,
            "created_at": datetime.now(UTC).isoformat(),
            "ttl_seconds": ttl_seconds,
            "metadata_json": json.dumps(metadata) if metadata else "",
        }

        table = self._get_table()
        try:
            table.add([entry])
            logger.debug(
                "Stored page cache entry (url=%s, method=%s, words=%d, ttl=%ds)",
                canonical_url[:50],
                extraction_method,
                word_count,
                ttl_seconds,
            )
        except Exception as exc:
            logger.warning("Failed to store page cache entry: %s", exc)


# Singleton instance (lazy init)
_PAGE_CACHE: PageCache | None = None


def get_page_cache(db_path: str | None = None) -> PageCache:
    """Get or create the page cache singleton."""
    global _PAGE_CACHE
    if _PAGE_CACHE is None:
        from ..settings import settings
        actual_path = db_path or settings.lancedb_dir
        _PAGE_CACHE = PageCache(db_path=actual_path)
        logger.info("Initialized page cache at %s", actual_path)
    return _PAGE_CACHE