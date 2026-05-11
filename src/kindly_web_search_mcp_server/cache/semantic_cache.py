"""Semantic cache operations with TTL checking."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime

from .content_type import ADAPTIVE_TTL, ADAPTIVE_TTL_SECONDS, ContentType, classify_content_type
from .store import SemanticCacheStore
from ..embeddings import embed_query

logger = logging.getLogger(__name__)


async def get_semantic_cache(
    store: SemanticCacheStore,
    query: str,
    min_score: float = 0.82,
    use_hybrid: bool = True,
    provider_key: str = "default",
) -> dict | None:
    """Look up semantic cache with hybrid search and TTL checking.

    Performs hybrid search combining vector similarity and full-text search,
    then validates TTL based on content type.

    Args:
        store: The SemanticCacheStore instance.
        query: The search query text.
        min_score: Minimum similarity score for a cache hit.
        use_hybrid: Whether to use hybrid search (vs vector-only).

    Returns:
        Cached result dict with keys: answer_json, similarity_score,
        cached_at, content_type, ttl_seconds, needs_validation.
        Returns None if no valid cache hit found.
    """
    try:
        # Generate query embedding
        query_embedding = await embed_query(query)
    except Exception as e:
        logger.warning("Embedding generation failed: %s, skipping cache lookup", e)
        return None

    # Perform search
    if use_hybrid and query:
        results = store.hybrid_search(query_embedding, query, provider_key=provider_key, limit=5)
    else:
        results = store.vector_search(query_embedding, provider_key=provider_key, limit=5)

    if not results:
        logger.debug("No cache results found for query: %s", query[:100])
        return None

    # Find best result by similarity score
    best_row = None
    best_similarity = 0.0
    for row in results:
        distance = row.get("_distance", 1.0)
        similarity = 1.0 - min(distance, 1.0)
        if similarity > best_similarity:
            best_similarity = similarity
            best_row = row

    if best_row is None or best_similarity < min_score:
        logger.debug(
            "No cache hit (similarity=%.2f < min_score=%.2f)",
            best_similarity,
            min_score,
        )
        return None

    # Check TTL based on content type
    content_type = ContentType(best_row.get("content_type", "general"))
    ttl_seconds = ADAPTIVE_TTL_SECONDS.get(content_type, 12 * 3600)
    created = datetime.fromisoformat(best_row["created_at"])
    age_seconds = (datetime.now(UTC) - created).total_seconds()

    if age_seconds > ttl_seconds:
        logger.debug(
            "Cache expired (age=%.0fs > ttl=%ds) for content_type=%s",
            age_seconds,
            ttl_seconds,
            content_type,
        )
        return None

    # Valid cache hit
    needs_validation = age_seconds > 24 * 3600  # Flag entries older than 24h
    logger.debug(
        "Cache hit (similarity=%.2f, age=%.0fs, content_type=%s)",
        best_similarity,
        age_seconds,
        content_type,
    )

    return {
        "answer_json": best_row["answer_json"],
        "similarity_score": best_similarity,
        "cached_at": best_row["created_at"],
        "content_type": content_type.value,
        "ttl_seconds": ttl_seconds,
        "needs_validation": needs_validation,
    }


async def set_semantic_cache(
    store: SemanticCacheStore,
    query: str,
    response: dict,
    content_type: ContentType | None = None,
    provider_key: str = "default",
) -> None:
    """Store a result in the semantic cache.

    Args:
        store: The SemanticCacheStore instance.
        query: The original query text.
        response: The response data to cache (will be JSON serialized).
        content_type: Content type classification. If None, will be
            classified automatically using query keywords.
    """
    try:
        # Generate query embedding
        query_embedding = await embed_query(query)
    except Exception as e:
        logger.warning("Embedding generation failed: %s, skipping cache storage", e)
        return

    # Classify content type if not provided
    if content_type is None:
        content_type = classify_content_type(query)

    # Generate hash and id
    query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
    entry_id = uuid.uuid4().hex

    # Serialize response to JSON
    answer_json = json.dumps(response)

    # Add entry to cache
    store.add_entry(
        id=entry_id,
        query_hash=query_hash,
        query_text=query,
        answer_json=answer_json,
        provider_key=provider_key,
        content_type=content_type.value,
        created_at=datetime.now(UTC).isoformat(),
        embedding=query_embedding,
    )

    logger.debug(
        "Stored cache entry %s (content_type=%s, ttl=%s)",
        entry_id,
        content_type,
        ADAPTIVE_TTL.get(content_type),
    )
