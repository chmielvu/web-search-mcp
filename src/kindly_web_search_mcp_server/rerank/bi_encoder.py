"""Bi-encoder based candidate filtering for large result sets."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, replace

import numpy as np

from ..ml import (
    EmbeddingAPIError,
    EmbeddingTimeoutError,
    embed_query,
    embed_texts,
)
from ..search.evidence import render_search_hit_text
from ..search.types import ScoredHit
from ..settings import settings
from ..utils.url_canonicalize import canonicalize_url
from .models import (
    CROSS_ENCODER_INPUT_LIMIT,
    CandidateEmbedding,
    RerankEmbeddingContext,
)

LOGGER = logging.getLogger(__name__)


def _candidate_embedding_text(candidate: ScoredHit, max_chars: int) -> str:
    return render_search_hit_text(candidate.hit, max_chars=max_chars)


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
    candidates: list[ScoredHit],
) -> tuple[list[ScoredHit], RerankEmbeddingContext | None]:
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
                url=canonicalize_url(candidate.hit.url),
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
    ranked_candidates = [
        replace(candidates[index], bi_encoder_score=float(similarities[index]))
        for index in ranked_indices
    ]
    return ranked_candidates, embedding_ctx


@dataclass(frozen=True, slots=True)
class ConditionalBiOutcome:
    candidates: list[ScoredHit]
    embedding_context: RerankEmbeddingContext | None
    duration_seconds: float
    status: str


async def run_conditional_bi_encoder(
    query: str,
    candidates: list[ScoredHit],
    *,
    precomputed_embedding: list[float] | None,
    logger: logging.Logger,
) -> ConditionalBiOutcome:
    """Rank pools above the cross limit; otherwise preserve hybrid-RRF order."""
    limit = CROSS_ENCODER_INPUT_LIMIT
    if len(candidates) <= limit:
        return ConditionalBiOutcome(
            candidates=list(candidates),
            embedding_context=None,
            duration_seconds=0.0,
            status="candidate_count_not_above_cross_limit",
        )

    started = time.monotonic()
    try:
        query_embedding = precomputed_embedding or await embed_query(
            query,
            timeout=settings.rerank_bi_encoder_timeout_seconds,
        )
    except Exception as exc:
        logger.warning("Bi-encoder query embedding failed: %s: %s", type(exc).__name__, exc)
        return ConditionalBiOutcome(
            candidates=list(candidates[:limit]),
            embedding_context=None,
            duration_seconds=time.monotonic() - started,
            status="query_embedding_failure",
        )

    ranked, embedding_context = await bi_encoder_rank(query_embedding, candidates)
    if embedding_context is None:
        ranked = list(candidates)
        status = "candidate_embedding_failure"
    else:
        status = "applied"
    return ConditionalBiOutcome(
        candidates=ranked[:limit],
        embedding_context=embedding_context,
        duration_seconds=time.monotonic() - started,
        status=status,
    )
