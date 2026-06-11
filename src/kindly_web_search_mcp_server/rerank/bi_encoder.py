"""Bi-encoder based candidate filtering for large result sets."""

from __future__ import annotations

import logging

import numpy as np

from ..embeddings import embed_texts
from ..embeddings.hf_inference import EmbeddingAPIError, EmbeddingTimeoutError
from ..models import WebSearchResult
from .models import CandidateEmbedding, RerankEmbeddingContext

LOGGER = logging.getLogger(__name__)


async def bi_encoder_filter(
    query_embedding: list[float],
    candidates: list[WebSearchResult],
    top_k: int = 100,
) -> tuple[list[WebSearchResult], RerankEmbeddingContext | None]:
    """
    Filter a large candidate list using embedding-based similarity scoring.

    The query embedding must be pre-computed by the caller and passed in.
    Returns the filtered candidates plus an embedding context so downstream
    stages (MMR diversity, Qdrant index) can reuse the vectors.

    Args:
        query_embedding: Pre-computed query embedding vector.
        candidates: List of web search results to filter.
        top_k: Number of top candidates to return.

    Returns:
        Tuple of (ranked_candidates, RerankEmbeddingContext or None).
        The context is None when no embedding was computed (fallback path).
    """
    if len(candidates) <= top_k:
        return candidates, None

    # Generate candidate embeddings (title + snippet)
    candidate_texts = [
        f"{candidate.title}\n{candidate.snippet}" for candidate in candidates
    ]

    try:
        candidate_vectors = await embed_texts(candidate_texts, timeout=60.0)
    except (EmbeddingTimeoutError, EmbeddingAPIError, Exception) as e:
        LOGGER.warning(
            f"Bi-encoder candidate embedding failed: {type(e).__name__}: {e}, falling back to top_k slice"
        )
        return candidates[:top_k], None

    if not candidate_vectors or len(candidate_vectors) != len(candidates):
        LOGGER.warning(
            f"Bi-encoder candidate embedding mismatch: got {len(candidate_vectors) if candidate_vectors else 0}, "
            f"expected {len(candidates)}, falling back to top_k slice"
        )
        return candidates[:top_k], None

    # Build embedding context before filtering (retains embeddings for ALL candidates)
    embedding_ctx = RerankEmbeddingContext(
        query_embedding=query_embedding,
        candidates=[
            CandidateEmbedding(
                url=candidate.link.strip(),
                text=text,
                dense=vec,
            )
            for candidate, text, vec in zip(
                candidates, candidate_texts, candidate_vectors
            )
        ],
    )

    # Compute cosine similarity
    query_normalized = np.array(query_embedding) / max(
        np.linalg.norm(query_embedding), 1e-12
    )
    matrix = np.array(candidate_vectors)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    similarities = (matrix / norms) @ query_normalized

    # Get top_k indices
    top_indices = np.argsort(similarities)[-top_k:][::-1].tolist()

    # Return candidates in ranked order
    return [candidates[index] for index in top_indices], embedding_ctx
