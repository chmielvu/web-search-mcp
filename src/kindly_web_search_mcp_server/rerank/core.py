"""Core reranking orchestration pipeline."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from typing import Any

import numpy as np

from ..embeddings import embed_query, embed_texts
from ..embeddings.hf_inference import (
    EmbeddingAPIError,
    EmbeddingTimeoutError,
    CircuitOpenError,
)
from ..models import WebSearchResult
from ..settings import settings
from ..telemetry import (
    record_rerank_stage,
    RERANK_STAGE,
    RERANK_INPUT_COUNT,
    RERANK_OUTPUT_COUNT,
    SEARCH_QUERY,
)
from .bi_encoder import bi_encoder_filter
from .diversity import maximal_marginal_relevance_rank
from .jina import jina_rerank
from .observability import emit_rerank_summary
from .voyage import voyage_rerank
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


def _normalize_scores_minmax(scores: list[float]) -> list[float]:
    """Min-max normalize scores to [0,1] range."""
    if not scores:
        return []
    arr = np.array(scores)
    min_s, max_s = arr.min(), arr.max()
    if max_s - min_s < 1e-9:
        return [0.5] * len(scores)
    return ((arr - min_s) / (max_s - min_s)).tolist()


def _compute_recency_score(
    published_date: str | None, half_life_days: int = 90
) -> float:
    """Compute recency score using exponential decay.

    Returns value in [0, 1] where 1.0 = today, 0.0 = very old.
    Formula: exp(-age_days / half_life_days)
    """
    if not published_date:
        return 0.0
    try:
        pub_dt = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
        now = datetime.now(pub_dt.tzinfo) if pub_dt.tzinfo else datetime.now()
        age_days = (now - pub_dt).days
        if age_days < 0:
            return 1.0
        return math.exp(-age_days / half_life_days)
    except (ValueError, AttributeError, TypeError):
        return 0.0


async def rerank_results(
    query: str,
    candidates: list[WebSearchResult],
    top_k: int = 10,
    *,
    searxng_time_range: str | None = None,
) -> list[WebSearchResult]:
    """
    Rerank web search results with multi-stage pipeline.

    Pipeline stages:
    1. Bi-encoder filtering (when candidates > top_k * 2)
       - Filter using embedding similarity down to top_k * 2 candidates
         so the cross-encoder (Stage 2) has a richer pool to reorder.
    2. Jina API reranking
       - Use jina-reranker-v3 by default
       - Min-max normalize scores to [0,1]
       - Apply recency bonus (unless searxng_time_range is set)
    3. Diversity pruning
       - Remove near-duplicates using embedding similarity

    The query embedding is computed once and shared across Stage 1 and
    Stage 3 to avoid redundant network calls.

    Args:
        query: Original search query
        candidates: List of web search results to rerank
        top_k: Final number of results to return
        searxng_time_range: If set, recency scoring is disabled (user already filtered by time)

    Returns:
        Reranked list of web search results (max top_k items).
        Returns original candidates[:top_k] on pipeline failures (graceful degradation).
    """
    if not candidates:
        return []

    if len(candidates) <= top_k:
        logger.debug(
            f"Candidates ({len(candidates)}) <= top_k ({top_k}), skipping rerank"
        )
        return candidates

    original_count = len(candidates)
    pipeline_start = time.time()
    logger.info(
        f"Starting rerank pipeline: {original_count} candidates, target top_k={top_k}"
    )

    # Compute query embedding once here. Both Stage 1 (bi-encoder) and
    # Stage 3 (MMR diversity) consume this vector — there is no second call.
    # If this fails, both embedding-dependent stages are skipped gracefully.
    query_embedding: list[float] | None = None
    try:
        # Use lower timeout (15s) for query embedding - critical path
        query_embedding = await embed_query(query, timeout=15.0)
    except (EmbeddingTimeoutError, EmbeddingAPIError, CircuitOpenError, Exception) as e:
        logger.warning(
            f"Query embedding failed: {type(e).__name__}: {e}; "
            "Stage 1 (bi-encoder) and Stage 3 (diversity) will be skipped"
        )

    # Create main rerank span
    with tracer.start_as_current_span(
        "rerank.pipeline",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            SEARCH_QUERY: query[:500],
            RERANK_INPUT_COUNT: original_count,
            "rerank.top_k": top_k,
        },
    ) as main_span:
        # Stage 1: Bi-encoder filtering (only if many candidates and embedding available).
        # Filter to top_k * 2 to give the cross-encoder (Stage 2) a wider pool.
        stage1_output_count = original_count
        stage1_duration = 0.0
        if query_embedding and len(candidates) > top_k * 2:
            bi_encoder_top_k = top_k * 2
            logger.debug(
                f"Stage 1: Bi-encoder filtering ({len(candidates)} > {top_k * 2}), keeping {bi_encoder_top_k}"
            )
            stage1_start = time.time()
            try:
                candidates = await bi_encoder_filter(
                    query_embedding,
                    candidates,
                    top_k=bi_encoder_top_k,
                )
                stage1_output_count = len(candidates)
            except Exception as e:
                logger.warning(
                    f"Bi-encoder filter failed: {type(e).__name__}: {e}, using top_k*2 slice"
                )
                candidates = candidates[:bi_encoder_top_k]
                stage1_output_count = len(candidates)
            stage1_duration = time.time() - stage1_start
            logger.debug(f"After bi-encoder: {len(candidates)} candidates")

            # Record Stage 1 telemetry
            record_rerank_stage(
                stage="bi_encoder",
                input_count=original_count,
                output_count=stage1_output_count,
                duration_seconds=stage1_duration,
            )

            main_span.add_event(
                "rerank.bi_encoder",
                attributes={
                    RERANK_STAGE: "bi_encoder",
                    RERANK_INPUT_COUNT: original_count,
                    RERANK_OUTPUT_COUNT: stage1_output_count,
                },
            )

        # Stage 2: Provider reranking
        logger.debug("Stage 2: provider reranking")
        stage2_input_count = len(candidates)
        stage2_output_count = len(candidates)
        stage2_duration = 0.0
        relevance_scores: list[float] = []
        stage2_provider = settings.rerank_provider.strip().lower()
        if stage2_provider not in {"voyage", "jina"}:
            raise ValueError(f"Unsupported rerank provider: {stage2_provider}")
        stage2_model = (
            settings.voyage_rerank_model
            if stage2_provider == "voyage"
            else settings.jina_rerank_model
        )
        fallback_provider = "jina" if stage2_provider == "voyage" else "voyage"
        backend_order = [stage2_provider, fallback_provider]

        documents = [
            f"Title: {candidate.title}\nURL: {candidate.link}\nSnippet: {candidate.snippet}"
            for candidate in candidates
        ]

        stage2_start = time.time()
        backend_error: Exception | None = None
        for backend in backend_order:
            try:
                if backend == "voyage":
                    ranked_indices = await voyage_rerank(
                        query,
                        documents,
                        timeout=30.0,
                        api_key=settings.voyage_api_key or None,
                        model=settings.voyage_rerank_model,
                    )
                else:
                    ranked_indices = await jina_rerank(
                        query,
                        documents,
                        timeout=30.0,
                        api_key=None,
                        model=settings.jina_rerank_model,
                    )
                stage2_provider = backend
                stage2_model = (
                    settings.voyage_rerank_model
                    if backend == "voyage"
                    else settings.jina_rerank_model
                )
                break
            except Exception as e:
                backend_error = e
                logger.warning(
                    "%s rerank failed: %s: %s, trying fallback provider",
                    backend.capitalize(),
                    type(e).__name__,
                    e,
                )
        else:
            ranked_indices = []

        if ranked_indices:
            sorted_ranked = sorted(ranked_indices, key=lambda x: x[1], reverse=True)

            raw_scores = [score for _, score in sorted_ranked]
            normalized_scores = (
                raw_scores
                if stage2_provider == "voyage"
                else _normalize_scores_minmax(raw_scores)
            )

            recency_weight = settings.rerank_recency_weight
            half_life_days = settings.rerank_recency_half_life_days
            apply_recency = searxng_time_range is None and recency_weight > 0

            if apply_recency:
                logger.debug(
                    f"Recency scoring enabled: weight={recency_weight}, half_life={half_life_days}d"
                )
            elif searxng_time_range is not None:
                logger.debug(
                    f"Recency scoring disabled: searxng_time_range={searxng_time_range!r}"
                )

            for (idx, _raw_score), norm_score in zip(sorted_ranked, normalized_scores, strict=False):
                final_score = norm_score
                if apply_recency:
                    recency_score = _compute_recency_score(
                        candidates[idx].published_date, half_life_days
                    )
                    final_score = norm_score + recency_weight * recency_score
                candidates[idx] = candidates[idx].model_copy(
                    update={"score": final_score}
                )

            candidates = [candidates[idx] for idx, _ in sorted_ranked]
            relevance_scores = [
                c.score
                for c in candidates[: min(10, len(candidates))]
                if c.score is not None
            ]
            stage2_output_count = len(candidates)
        elif backend_error is not None:
            logger.warning(
                "All rerank providers failed; preserving merged candidate order: %s",
                backend_error,
            )
        stage2_duration = time.time() - stage2_start

        max_rerank_score: float = 0.0
        if relevance_scores:
            max_rerank_score = max(relevance_scores)

        logger.debug(f"After provider rerank: {len(candidates)} candidates")

        # Record Stage 2 telemetry
        if relevance_scores:
            record_rerank_stage(
                stage=stage2_provider,
                input_count=stage2_input_count,
                output_count=stage2_output_count,
                duration_seconds=stage2_duration,
                relevance_scores=relevance_scores,
                model=stage2_model,
            )

            main_span.add_event(
                f"rerank.{stage2_provider}",
                attributes={
                    RERANK_STAGE: stage2_provider,
                    RERANK_INPUT_COUNT: stage2_input_count,
                    RERANK_OUTPUT_COUNT: stage2_output_count,
                    "rerank.model": stage2_model,
                    "rerank.top_score": round(max(relevance_scores), 4),
                    "rerank.avg_score": round(
                        sum(relevance_scores) / len(relevance_scores), 4
                    ),
                },
            )

        # Stage 3: Diversity pruning (requires query embedding for MMR).
        logger.debug("Stage 3: Diversity pruning")
        stage3_input_count = len(candidates[: top_k * 2])
        stage3_output_count = stage3_input_count
        stage3_duration = 0.0
        diversity_removed = 0

        if query_embedding:
            texts = [
                f"{candidate.title}\n{candidate.snippet}"
                for candidate in candidates[: top_k * 2]
            ]
            stage3_start = time.time()
            try:
                embeddings = await embed_texts(texts, timeout=10.0)
                if embeddings and len(embeddings) == len(candidates[: top_k * 2]):
                    scoped_urls = [
                        candidate.link for candidate in candidates[: top_k * 2]
                    ]

                    diversified_rank = maximal_marginal_relevance_rank(
                        query_embedding,
                        embeddings,
                        scoped_urls,
                        lambda_param=settings.mmr_lambda_param,
                        max_per_host=2,
                    )

                    candidates = [
                        candidates[i] for i in diversified_rank[: top_k * 2]
                    ] + candidates[top_k * 2 :]
                    stage3_output_count = len(candidates)
                    diversity_removed = len(diversified_rank) - len(
                        diversified_rank[: top_k * 2]
                    )
                    logger.debug(
                        f"After diversity pruning: {len(candidates)} candidates"
                    )
                else:
                    logger.warning(
                        f"Diversity embedding mismatch: got {len(embeddings) if embeddings else 0}, "
                        f"expected {len(candidates[: top_k * 2])}, skipping diversity stage"
                    )
            except (
                EmbeddingTimeoutError,
                EmbeddingAPIError,
                CircuitOpenError,
                Exception,
            ) as e:
                logger.warning(
                    f"Diversity embedding failed: {type(e).__name__}: {e}, skipping diversity stage"
                )
            stage3_duration = time.time() - stage3_start

        # Record Stage 3 telemetry
        record_rerank_stage(
            stage="diversity",
            input_count=stage3_input_count,
            output_count=stage3_output_count,
            duration_seconds=stage3_duration,
        )

        main_span.add_event(
            "rerank.diversity",
            attributes={
                RERANK_STAGE: "diversity",
                RERANK_INPUT_COUNT: stage3_input_count,
                RERANK_OUTPUT_COUNT: stage3_output_count,
                "rerank.removed_count": diversity_removed,
            },
        )

        # Return final top_k results with an optional score floor.
        score_threshold = settings.rerank_score_threshold
        final_results = [
            r for r in candidates if r.score is None or r.score >= score_threshold
        ][:top_k]

        main_span.set_attribute("rerank.final_count", len(final_results))

        logger.info(
            f"Rerank pipeline complete: {original_count} -> {len(final_results)} results"
        )
        emit_rerank_summary(
            logger,
            query=query,
            input_count=original_count,
            output=final_results,
            top_k=top_k,
            duration_seconds=time.time() - pipeline_start,
            score_threshold=score_threshold,
            provider=stage2_provider,
            model=stage2_model,
            max_score=max_rerank_score,
        )

        return final_results
