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
from ..utils.observability import emit_observability_event
from .bi_encoder import bi_encoder_filter
from .diversity import maximal_marginal_relevance_rank
from .engines import rerank_with_engine_fallback
from .observability import emit_rerank_summary
from .policy import decide_rerank
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


def _normalize_scores_minmax(scores: list[float]) -> list[float]:
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
    query_entities: list | None = None,
) -> list[WebSearchResult]:
    """Rerank web search results with bi-encoder, provider, and diversity stages.

    query_entities (if provided) enables the measured entity-overlap feature
    when KINDLY_RERANK_ENTITY_OVERLAP_ENABLED.
    """
    if not candidates:
        return []

    if len(candidates) <= top_k:
        logger.debug(
            f"Candidates ({len(candidates)}) <= top_k ({top_k}), skipping rerank"
        )
        return candidates

    decision = decide_rerank(
        query=query, candidate_count=len(candidates), top_k=top_k
    )
    if not decision.should_rerank:
        logger.info(
            "Rerank bypassed by policy: reason=%s count=%s",
            decision.reason,
            len(candidates),
        )
        # policy already emitted rerank.eligibility + rerank.bypassed
        emit_observability_event(
            logger,
            "rerank.bypassed",
            reason=decision.reason,
            query=query[:200],
            candidate_count=len(candidates),
        )
        return candidates

    original_count = len(candidates)
    pipeline_start = time.time()
    logger.info(
        f"Starting rerank pipeline: {original_count} candidates, target top_k={top_k}"
    )

    query_embedding: list[float] | None = None
    try:
        query_embedding = await embed_query(query, timeout=15.0)
    except (EmbeddingTimeoutError, EmbeddingAPIError, CircuitOpenError, Exception) as e:
        logger.warning(
            f"Query embedding failed: {type(e).__name__}: {e}; "
            "Stage 1 (bi-encoder) and Stage 3 (diversity) will be skipped"
        )

    with tracer.start_as_current_span(
        "rerank.pipeline",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            SEARCH_QUERY: query[:500],
            RERANK_INPUT_COUNT: original_count,
            "rerank.top_k": top_k,
        },
    ) as main_span:
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

        logger.debug("Stage 2: provider reranking")
        stage2_input_count = len(candidates)
        stage2_output_count = len(candidates)
        stage2_duration = 0.0
        relevance_scores: list[float] = []
        configured_provider = settings.rerank_provider.strip().lower()
        stage2_provider = configured_provider
        stage2_model = None

        stage2_start = time.time()
        rerank_outcome = await rerank_with_engine_fallback(
            query,
            candidates,
            engine_id=configured_provider,
        )
        stage2_provider = rerank_outcome.engine_id
        stage2_model = rerank_outcome.model

        if rerank_outcome.ranked:
            sorted_ranked = sorted(
                rerank_outcome.ranked, key=lambda result: result.score, reverse=True
            )

            raw_scores = [result.score for result in sorted_ranked]
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

            for ranked_result, norm_score in zip(
                sorted_ranked, normalized_scores, strict=False
            ):
                idx = ranked_result.index
                final_score = norm_score
                if apply_recency:
                    recency_score = _compute_recency_score(
                        candidates[idx].published_date, half_life_days
                    )
                    final_score = norm_score + recency_weight * recency_score
                candidates[idx] = candidates[idx].model_copy(
                    update={"score": final_score}
                )

            candidates = [candidates[item.index] for item in sorted_ranked]

            # Entity overlap as measured rerank signal (Phase 8.3)
            # Blended only when KINDLY_RERANK_ENTITY_OVERLAP_ENABLED; additive on top of
            # cross-encoder + recency. Weight controlled in settings. Emits for dashboards.
            if getattr(settings, "rerank_entity_overlap_enabled", False) and query_entities:
                try:
                    from ..entity.overlap import compute_entity_overlap

                    w = float(getattr(settings, "rerank_entity_overlap_weight", 0.15))
                    os_list: list[float] = []
                    for c in candidates[: min(20, len(candidates))]:
                        c_ents = getattr(c, "entities", None) or []
                        o = compute_entity_overlap(query_entities, c_ents if isinstance(c_ents, (list, tuple)) else [])
                        os_list.append(o)
                        if getattr(c, "score", None) is not None:
                            c.score = float(c.score) + (w * o)
                    if os_list:
                        emit_observability_event(
                            logger,
                            "rerank.entity_overlap",
                            query=query,
                            mean_overlap=sum(os_list) / len(os_list),
                            min_overlap=min(os_list),
                            max_overlap=max(os_list),
                            weight=w,
                            enabled=True,
                        )
                except Exception as exc:
                    logger.debug("entity overlap rerank (measured) skipped: %s", exc)
            relevance_scores = [
                c.score
                for c in candidates[: min(10, len(candidates))]
                if c.score is not None
            ]
            stage2_output_count = len(candidates)
        elif rerank_outcome.error is not None:
            logger.warning(
                "All rerank providers failed; preserving merged candidate order: %s",
                rerank_outcome.error,
            )
        stage2_duration = time.time() - stage2_start

        max_rerank_score: float = 0.0
        if relevance_scores:
            max_rerank_score = max(relevance_scores)

        logger.debug(f"After provider rerank: {len(candidates)} candidates")

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

        emit_observability_event(
            logger,
            "rerank.completed",
            query=query[:200],
            input_count=original_count,
            output_count=len(final_results),
            bypassed=False,
            reason="policy_eligible",
        )

        return final_results
