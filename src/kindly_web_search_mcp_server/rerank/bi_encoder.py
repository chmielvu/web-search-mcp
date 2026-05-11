"""Bi-encoder based candidate filtering for large result sets."""

from __future__ import annotations

import logging

import numpy as np

from ..embeddings import embed_texts
from ..embeddings.hf_inference import EmbeddingAPIError, EmbeddingTimeoutError
from ..models import WebSearchResult

LOGGER = logging.getLogger(__name__)


async def bi_encoder_filter(
    query_embedding: list[float],
    candidates: list[WebSearchResult],
    top_k: int = 100,
) -> list[WebSearchResult]:
    """
    Filter a large candidate list using embedding-based similarity scoring.

    The query embedding must be pre-computed by the caller and passed in.
    This keeps the single point of embedding computation in the orchestrating
    pipeline (rerank_results), so the HF Inference call happens exactly once.

    Args:
        query_embedding: Pre-computed query embedding vector.
        candidates: List of web search results to filter.
        top_k: Number of top candidates to return.

    Returns:
        Filtered list of candidates sorted by cosine similarity to the query,
        or the leading top_k slice of candidates if candidate embedding fails
        (graceful degradation).
    """
    if len(candidates) <= top_k:
        return candidates

    # Generate candidate embeddings (title + snippet)
    candidate_texts = [
        f"{candidate.title}\n{candidate.snippet}" for candidate in candidates
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
    query_normalized = np.array(query_embedding) / max(np.linalg.norm(query_embedding), 1e-12)
    matrix = np.array(candidate_vectors)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    similarities = (matrix / norms) @ query_normalized

    # Get top_k indices
    top_indices = np.argsort(similarities)[-top_k:][::-1].tolist()

    # Return candidates in ranked order
    return [candidates[index] for index in top_indices]
