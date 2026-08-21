"""Bi-encoder based candidate filtering for large result sets."""

from __future__ import annotations

import asyncio
import logging
import math
import time

import numpy as np

from ..embeddings import (
    EmbeddingAPIError,
    EmbeddingTimeoutError,
    embed_texts,
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


async def bi_encoder_rank(
    query_embedding: list[float],
    candidates: list[WebSearchResult],
) -> tuple[list[WebSearchResult], RerankEmbeddingContext | None]:
    """Rank the complete pool and retain embeddings for downstream diversity."""
    max_chars = max(1, int(settings.rerank_bi_encoder_text_max_chars))
    candidate_texts = [_candidate_embedding_text(candidate, max_chars) for candidate in candidates]
    try:
        embed_started = time.monotonic()
        candidate_vectors = await _embed_candidate_texts(candidate_texts)
        LOGGER.info(
            "bi_encoder embed_texts took %.2fs for %d texts across %d batches",
            time.monotonic() - embed_started,
            len(candidate_texts),
            math.ceil(len(candidate_texts) / settings.rerank_bi_encoder_batch_size),
        )
        if not candidate_vectors or len(candidate_vectors) != len(candidates):
            raise ValueError(
                f"candidate embedding count mismatch: {len(candidate_vectors)} != {len(candidates)}"
            )
        matrix = np.asarray(candidate_vectors, dtype=float)
        query_vector = np.asarray(query_embedding, dtype=float)
        if matrix.ndim != 2 or query_vector.ndim != 1 or matrix.shape[1] != query_vector.shape[0]:
            raise ValueError("query and candidate embedding dimensions do not match")
        query_norm = np.linalg.norm(query_vector)
        if query_norm == 0:
            raise ValueError("query embedding has zero norm")
        norms = np.linalg.norm(matrix, axis=1)
        similarities = np.divide(
            matrix @ (query_vector / query_norm),
            norms,
            out=np.zeros(len(candidates), dtype=float),
            where=norms != 0,
        )
    except (EmbeddingTimeoutError, EmbeddingAPIError) as exc:
        LOGGER.warning(
            "Bi-encoder candidate embedding failed: %s: %s",
            type(exc).__name__,
            exc,
        )
        return list(candidates), None
    except Exception as exc:
        LOGGER.warning("Bi-encoder ranking failed: %s: %s", type(exc).__name__, exc)
        return list(candidates), None

    embedding_ctx = RerankEmbeddingContext(
        query_embedding=query_embedding,
        candidates=[
            CandidateEmbedding(
                url=candidate.link.strip(),
                text=text,
                dense=vector,
            )
            for candidate, text, vector in zip(
                candidates,
                candidate_texts,
                candidate_vectors,
                strict=True,
            )
        ],
    )
    ranked_indices = sorted(
        range(len(candidates)),
        key=lambda index: (-float(similarities[index]), index),
    )
    return [candidates[index] for index in ranked_indices], embedding_ctx


async def bi_encoder_filter(
    query_embedding: list[float],
    candidates: list[WebSearchResult],
    top_k: int = 100,
) -> tuple[list[WebSearchResult], RerankEmbeddingContext | None]:
    """Compatibility filter over the complete bi-encoder ranking."""
    ranked, embedding_ctx = await bi_encoder_rank(query_embedding, candidates)
    return ranked[:top_k], embedding_ctx
