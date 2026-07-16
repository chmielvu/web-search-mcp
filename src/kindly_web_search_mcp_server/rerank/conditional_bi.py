"""Strict large-pool bi-encoder guardrail."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time

from ..embeddings import embed_query
from ..models import WebSearchResult
from ..settings import settings
from .bi_encoder import bi_encoder_rank
from .models import RerankEmbeddingContext


@dataclass(frozen=True, slots=True)
class ConditionalBiOutcome:
    candidates: list[WebSearchResult]
    embedding_context: RerankEmbeddingContext | None
    duration_seconds: float
    status: str


async def run_conditional_bi_encoder(
    query: str,
    candidates: list[WebSearchResult],
    *,
    precomputed_embedding: list[float] | None,
    logger: logging.Logger,
) -> ConditionalBiOutcome:
    """Rank pools above the cross limit; otherwise preserve hybrid-RRF order."""
    from .limits import CROSS_ENCODER_INPUT_LIMIT

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
