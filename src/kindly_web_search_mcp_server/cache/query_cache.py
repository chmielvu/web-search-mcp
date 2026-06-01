"""Exact query cache for deterministic search result caching.

Unlike the semantic cache which uses embeddings for fuzzy matching,
this cache provides exact key-based lookup for identical query parameters.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import lancedb

from ..telemetry import record_cache_lookup, CACHE_TYPE, CACHE_HIT

logger = logging.getLogger(__name__)

# Default TTL for exact query cache (24 hours)
QUERY_CACHE_DEFAULT_TTL_SECONDS = int(
    os.environ.get("KINDLY_QUERY_CACHE_TTL_SECONDS", "86400")
)

QUERY_CACHE_SCHEMA = [
    ("id", "string"),
    ("cache_key", "string"),  # Composite key hash
    ("normalized_query", "string"),
    ("num_results", "int64"),
    ("rewrite_enabled", "bool"),
    ("search_mode", "string"),
    ("providers_key", "string"),
    ("response_json", "string"),
    ("created_at", "string"),
    ("ttl_seconds", "int64"),
]


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
    key_parts = [
        normalized_query.strip().lower(),
        str(num_results),
        str(rewrite_enabled),
        search_mode.strip().lower(),
        providers_key.strip().lower(),
    ]
    composite = "|".join(key_parts)
    return hashlib.sha256(composite.encode()).hexdigest()[:32]


class ExactQueryCache:
    """LanceDB-backed exact query cache.

    Provides deterministic caching for identical search parameters,
    bypassing the semantic embedding lookup for exact matches.
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
                self._table = db.open_table("query_cache_v2")
                logger.debug("Opened existing query_cache_v2 table")
            except Exception:
                # Create with arrow schema
                import pyarrow as pa

                arrow_schema = pa.schema(
                    [
                        pa.field("id", pa.string()),
                        pa.field("cache_key", pa.string()),
                        pa.field("normalized_query", pa.string()),
                        pa.field("num_results", pa.int64()),
                        pa.field("rewrite_enabled", pa.bool_()),
                        pa.field("search_mode", pa.string()),
                        pa.field("providers_key", pa.string()),
                        pa.field("response_json", pa.string()),
                        pa.field("created_at", pa.string()),
                        pa.field("ttl_seconds", pa.int64()),
                    ]
                )
                self._table = db.create_table("query_cache_v2", schema=arrow_schema)
                logger.info("Created new query_cache_v2 table")
        return self._table

    def lookup(
        self,
        normalized_query: str,
        num_results: int,
        rewrite_enabled: bool,
        search_mode: str = "balanced",
        providers_key: str = "default",
    ) -> dict | None:
        """Look up an exact cache hit for the given parameters.

        Args:
            normalized_query: Normalized search query.
            num_results: Number of results requested.
            rewrite_enabled: Whether query rewriting was enabled.
            search_mode: Search mode (speed/balanced/quality).

        Returns:
            Cached response dict if found and not expired, else None.
        """
        start_time = time.time()
        cache_key = _compute_cache_key(
            normalized_query, num_results, rewrite_enabled, search_mode, providers_key
        )

        table = self._get_table()
        try:
            results = (
                table.search().where(f"cache_key = '{cache_key}'").limit(1).to_list()
            )
        except Exception as exc:
            logger.warning("Exact query cache lookup failed: %s", exc)
            # Record cache miss on lookup failure
            record_cache_lookup(cache_type="exact", hit=False)
            return None

        if not results:
            logger.debug("No exact cache hit for key: %s", cache_key[:16])
            # Record cache miss
            duration = time.time() - start_time
            record_cache_lookup(
                cache_type="exact", hit=False, duration_seconds=duration
            )
            return None

        row = results[0]

        # Check TTL
        created_at = datetime.fromisoformat(row["created_at"])
        age_seconds = (datetime.now(UTC) - created_at).total_seconds()
        ttl_seconds = row.get("ttl_seconds", QUERY_CACHE_DEFAULT_TTL_SECONDS)

        if age_seconds > ttl_seconds:
            logger.debug(
                "Exact cache expired (age=%.0fs > ttl=%ds)",
                age_seconds,
                ttl_seconds,
            )
            # Record expired as cache miss
            duration = time.time() - start_time
            record_cache_lookup(
                cache_type="exact", hit=False, duration_seconds=duration
            )
            return None

        # Record cache hit
        duration = time.time() - start_time
        record_cache_lookup(cache_type="exact", hit=True, duration_seconds=duration)

        logger.debug(
            "Exact cache hit (key=%s, age=%.0fs)",
            cache_key[:16],
            age_seconds,
        )

        try:
            return json.loads(row["response_json"])
        except json.JSONDecodeError:
            logger.warning("Failed to decode cached response JSON")
            # Record decode failure as miss
            record_cache_lookup(cache_type="exact", hit=False)
            return None

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
        """Store a search response in the exact query cache.

        Args:
            normalized_query: Normalized search query.
            num_results: Number of results requested.
            rewrite_enabled: Whether query rewriting was enabled.
            response: The search response to cache.
            search_mode: Search mode (speed/balanced/quality).
            ttl_seconds: TTL override. Uses default if None.
        """
        cache_key = _compute_cache_key(
            normalized_query, num_results, rewrite_enabled, search_mode, providers_key
        )

        if ttl_seconds is None:
            ttl_seconds = QUERY_CACHE_DEFAULT_TTL_SECONDS

        entry = {
            "id": uuid.uuid4().hex,
            "cache_key": cache_key,
            "normalized_query": normalized_query,
            "num_results": num_results,
            "rewrite_enabled": rewrite_enabled,
            "search_mode": search_mode,
            "providers_key": providers_key,
            "response_json": json.dumps(response),
            "created_at": datetime.now(UTC).isoformat(),
            "ttl_seconds": ttl_seconds,
        }

        table = self._get_table()
        try:
            table.add([entry])
            logger.debug(
                "Stored exact cache entry (key=%s, ttl=%ds)",
                cache_key[:16],
                ttl_seconds,
            )
        except Exception as exc:
            logger.warning("Failed to store exact cache entry: %s", exc)


# Singleton instance (lazy init)
_QUERY_CACHE: ExactQueryCache | None = None


def get_query_cache(db_path: str | None = None) -> ExactQueryCache:
    """Get or create the exact query cache singleton."""
    global _QUERY_CACHE
    if _QUERY_CACHE is None:
        from ..settings import settings

        actual_path = db_path or settings.lancedb_dir
        _QUERY_CACHE = ExactQueryCache(db_path=actual_path)
        logger.info("Initialized exact query cache at %s", actual_path)
    return _QUERY_CACHE
