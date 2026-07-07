"""Bi-encoder based candidate filtering for large result sets."""

from __future__ import annotations

import asyncio
import logging
import math
import time

import numpy as np

from ..embeddings import embed_texts
from ..embeddings.hf_inference import (
    EmbeddingAPIError,
    EmbeddingTimeoutError,
)
from ..models import WebSearchResult
from ..settings import settings
from .models import CandidateEmbedding, RerankEmbeddingContext

LOGGER = logging.getLogger(__name__)


def _candidate_embedding_text(candidate: WebSearchResult, max_chars: int) -> str:
    title = " ".join(candidate.title.split())
    snippet = " ".join(candidate.snippet.split())
    if max_chars <= 0:
        return title
    if not snippet:
        return title[:max_chars]

    separator = "\n"
    snippet_budget = max_chars - len(title) - len(separator)
    if snippet_budget <= 0:
        return title[:max_chars]
    if len(snippet) > snippet_budget:
        snippet = snippet[:snippet_budget].rstrip()
    return f"{title}{separator}{snippet}"


async def _embed_candidate_texts(candidate_texts: list[str]) -> list[list[float]]:
    batch_size = max(1, int(settings.rerank_bi_encoder_batch_size))
    max_concurrent = max(1, int(settings.rerank_bi_encoder_max_concurrent_batches))
    timeout = settings.rerank_bi_encoder_timeout_seconds
    batches = [
        candidate_texts[index : index + batch_size]
        for index in range(0, len(candidate_texts), batch_size)
    ]
    semaphore = asyncio.Semaphore(max_concurrent)

    async def embed_batch(batch: list[str]) -> list[list[float]]:
        async with semaphore:
            return await embed_texts(batch, timeout=timeout, max_retries=0)

    batch_vectors = await asyncio.gather(*(embed_batch(batch) for batch in batches))
    vectors: list[list[float]] = []
    for batch in batch_vectors:
        vectors.extend(batch)
    return vectors


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
        The context is None when embedding failed (fallback path).
    """
    # Generate candidate embeddings (title + snippet)
    max_chars = max(1, int(settings.rerank_bi_encoder_text_max_chars))
    candidate_texts = [_candidate_embedding_text(candidate, max_chars) for candidate in candidates]

    try:
        _embed_t0 = time.time()
        candidate_vectors = await _embed_candidate_texts(candidate_texts)
        LOGGER.info(
            "bi_encoder embed_texts took %.2fs for %d texts across %d batches",
            time.time() - _embed_t0,
            len(candidate_texts),
            math.ceil(len(candidate_texts) / settings.rerank_bi_encoder_batch_size),
        )
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
            for candidate, text, vec in zip(candidates, candidate_texts, candidate_vectors)
        ],
    )

    # If candidates fit within top_k, skip filtering but return embedding_ctx
    # so downstream stages (MMR diversity) can reuse the vectors.
    if len(candidates) <= top_k:
        return candidates, embedding_ctx

    # Compute cosine similarity
    query_normalized = np.array(query_embedding) / max(np.linalg.norm(query_embedding), 1e-12)
    matrix = np.array(candidate_vectors)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    similarities = (matrix / norms) @ query_normalized

    # Get top_k indices
    top_indices = np.argsort(similarities)[-top_k:][::-1].tolist()

    # Return candidates in ranked order
    return [candidates[index] for index in top_indices], embedding_ctx
