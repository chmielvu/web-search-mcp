"""LanceDB semantic cache store with hybrid search."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import lancedb
from lancedb.rerankers import RRFReranker

from .schema import SEMANTIC_CACHE_SCHEMA, SEMANTIC_CACHE_TABLE_NAME

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SemanticCacheStore:
    """LanceDB-backed semantic cache with hybrid search capabilities.

    Provides lazy connection, FTS indexing, and RRF reranking for
    optimal cache hit rates.
    """

    def __init__(self, db_path: str = "./lancedb_data") -> None:
        """Initialize the cache store.

        Args:
            db_path: Path to LanceDB database directory.
        """
        self.db_path = db_path
        self._db: lancedb.db.DBConnection | None = None
        self._cache_table: lancedb.table.Table | None = None
        self._fts_index_created = False

    def _get_db(self) -> lancedb.db.DBConnection:
        """Get or create LanceDB connection (lazy)."""
        if self._db is None:
            self._db = lancedb.connect(self.db_path)
        return self._db

    def _get_cache_table(self) -> lancedb.table.Table:
        """Get or create the semantic cache table."""
        if self._cache_table is None:
            db = self._get_db()
            try:
                self._cache_table = db.open_table(SEMANTIC_CACHE_TABLE_NAME)
                logger.debug("Opened existing %s table", SEMANTIC_CACHE_TABLE_NAME)
            except Exception:
                self._cache_table = db.create_table(
                    SEMANTIC_CACHE_TABLE_NAME,
                    schema=SEMANTIC_CACHE_SCHEMA,
                )
                logger.info("Created new %s table", SEMANTIC_CACHE_TABLE_NAME)

            # Ensure FTS index exists
            self._ensure_fts_index()

        return self._cache_table

    def _ensure_fts_index(self) -> None:
        """Create full-text search index on query_text if needed."""
        if self._fts_index_created:
            return

        table = self._get_cache_table()
        try:
            table.create_fts_index("query_text", replace=True)
            self._fts_index_created = True
            logger.info("FTS index created on query_text")
        except Exception as exc:
            logger.debug("FTS index creation skipped: %s", exc)

    def hybrid_search(
        self,
        query_embedding: list[float],
        query_text: str,
        provider_key: str = "default",
        limit: int = 5,
    ) -> list[dict]:
        """Perform hybrid search with RRF reranking.

        Combines vector similarity and full-text search, then reranks
        using Reciprocal Rank Fusion (RRF) with K=60.

        Args:
            query_embedding: Query embedding vector.
            query_text: Query text for full-text search.
            limit: Maximum number of results to return.

        Returns:
            List of result dictionaries from the cache.
        """
        table = self._get_cache_table()
        try:
            results = (
                table.search(query_type="hybrid")
                .vector(query_embedding)
                .text(query_text)
                .where(f"provider_key = '{provider_key}'")
                .rerank(RRFReranker(K=60))
                .limit(limit)
                .to_list()
            )
            logger.debug("Hybrid search returned %s results", len(results))
            return results
        except Exception as exc:
            logger.warning("Hybrid search failed: %s", exc)
            # Fallback to vector-only search
            return self.vector_search(query_embedding, provider_key=provider_key, limit=limit)

    def vector_search(
        self,
        query_embedding: list[float],
        provider_key: str = "default",
        limit: int = 5,
    ) -> list[dict]:
        """Perform vector-only search.

        Args:
            query_embedding: Query embedding vector.
            limit: Maximum number of results to return.

        Returns:
            List of result dictionaries from the cache.
        """
        table = self._get_cache_table()
        try:
            results = (
                table.search(query_embedding, vector_column_name="embedding")
                .where(f"provider_key = '{provider_key}'")
                .limit(limit)
                .to_list()
            )
            logger.debug("Vector search returned %s results", len(results))
            return results
        except Exception as exc:
            logger.warning("Vector search failed: %s", exc)
            return []

    def add_entry(
        self,
        id: str,  # noqa: A002
        query_hash: str,
        query_text: str,
        answer_json: str,
        provider_key: str,
        content_type: str,
        created_at: str,
        embedding: list[float],
    ) -> None:
        """Add a new entry to the semantic cache.

        Args:
            id: Unique identifier for the cache entry.
            query_hash: Hash of the query for deduplication.
            query_text: The original query text.
            answer_json: JSON-serialized answer data.
            provider_key: Normalized caller provider set for cache identity.
            content_type: Classification of the content.
            created_at: ISO timestamp when the entry was created.
            embedding: Query embedding vector.
        """
        table = self._get_cache_table()
        table.add(
            [
                {
                    "id": id,
                    "query_hash": query_hash,
                    "query_text": query_text,
                    "answer_json": answer_json,
                    "provider_key": provider_key,
                    "content_type": content_type,
                    "created_at": created_at,
                    "embedding": embedding,
                }
            ]
        )
        logger.debug("Added cache entry %s", id)

    def count_entries(self) -> int:
        """Count total entries in the cache.

        Returns:
            Number of entries in the cache table.
        """
        try:
            return self._get_cache_table().count_rows()
        except Exception as exc:
            logger.warning("Failed to count cache entries: %s", exc)
            return 0

    def close(self) -> None:
        """Close the LanceDB connection (cleanup resources)."""
        # LanceDB connections don't require explicit close in current version
        # Just reset internal references
        self._db = None
        self._cache_table = None
        self._fts_index_created = False
        logger.debug("Cleaned up LanceDB connection references")
