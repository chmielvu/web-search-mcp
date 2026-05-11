"""Bi-encoder based candidate filtering for large result sets."""

from __future__ import annotations

import logging

import numpy as np

from ..embeddings import embed_query, embed_texts
from ..embeddings.hf_inference import EmbeddingAPIError, EmbeddingTimeoutError
from ..models import WebSearchResult

LOGGER = logging.getLogger(__name__)


async def bi_encoder_filter(
    query: str,
    candidates: list[WebSearchResult],
    top_k: int = 100,
) -> list[WebSearchResult]:
    """
    Filter a large candidate list using embedding-based similarity scoring.

    This function is triggered when candidate count exceeds top_k * 2.
    It computes cosine similarity between query and candidate embeddings
    and returns the top_k most similar candidates.

    Args:
        query: Search query string
        candidates: List of web search results to filter
        top_k: Number of top candidates to return

    Returns:
        Filtered list of candidates sorted by relevance, or original candidates
        top_k if embedding fails (graceful degradation).
    """
    if len(candidates) <= top_k:
        return candidates

    # Generate query embedding
    try:
        query_vector = await embed_query(query, timeout=30.0)
    except (EmbeddingTimeoutError, EmbeddingAPIError, Exception) as e:
        LOGGER.warning(f"Bi-encoder query embedding failed: {type(e).__name__}: {e}, falling back to top_k slice")
        return candidates[:top_k]

    if not query_vector:
        LOGGER.warning("Bi-encoder query embedding returned empty, falling back to top_k slice")
        return candidates[:top_k]

    # Generate candidate embeddings (title + snippet)
    candidate_texts = [
        f"{candidate.title} {candidate.snippet}" for candidate in candidates
    ]

    try:
        candidate_vectors = await embed_texts(candidate_texts, timeout=60.0)
    except (EmbeddingTimeoutError, EmbeddingAPIError, Exception) as e:
        LOGGER.warning(f"Bi-encoder candidate embedding failed: {type(e).__name__}: {e}, falling back to top_k slice")
        return candidates[:top_k]

    if not candidate_vectors or len(candidate_vectors) != len(candidates):
        LOGGER.warning(
            f"Bi-encoder candidate embedding mismatch: got {len(candidate_vectors) if candidate_vectors else 0}, "
            f"expected {len(candidates)}, falling back to top_k slice"
        )
        return candidates[:top_k]

    # Compute cosine similarity
    query_normalized = np.array(query_vector) / max(np.linalg.norm(query_vector), 1e-12)
    matrix = np.array(candidate_vectors)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    similarities = (matrix / norms) @ query_normalized

    # Get top_k indices
    top_indices = np.argsort(similarities)[-top_k:][::-1].tolist()

    # Return candidates in ranked order
    ranked_candidates = [candidates[index] for index in top_indices]

    return ranked_candidates