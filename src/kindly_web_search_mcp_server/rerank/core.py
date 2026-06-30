"""Core reranking orchestration pipeline."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from opentelemetry import trace

from ..embeddings import embed_query
from ..embeddings.hf_inference import CircuitOpenError, EmbeddingAPIError, EmbeddingTimeoutError
from ..models import WebSearchResult
from ..prompts.rerank import build_rerank_instruction
from ..settings import settings
from ..telemetry import (
    INPUT_MIME_TYPE,
    INPUT_VALUE,
    RERANK_INPUT_COUNT,
    SEARCH_QUERY,
    record_rerank_stage,
)
from ..utils.observability import emit_observability_event
from .bi_encoder import bi_encoder_filter
from .models import RerankEmbeddingContext, RerankOutput
from .observability import emit_rerank_summary, record_rerank_candidate_rows
from .policy import decide_rerank
from .reporting import record_bi_encoder_stage, record_diversity_stage
from .stack import build_rerank_stack_plan
from .stage_runner import run_cross_encoder_stage, run_llm_stage
from .stages import run_diversity_pruning

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


def _normalize_instruction_text(text: str | None, max_length: int) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return None
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 1].rstrip() + "…"


def _build_rerank_instruction(
    *,
    research_goal: str | None = None,
    query_type_hint: str | None = None,
) -> str | None:
    return build_rerank_instruction(
        query="",
        query_type=(query_type_hint or "general").strip().lower(),
        research_goal=_normalize_instruction_text(research_goal, 180),
    )


async def rerank_results(
    query: str,
    candidates: list[WebSearchResult],
    top_k: int = 10,
    *,
    searxng_time_range: str | None = None,
    query_entities: list | None = None,
    research_goal: str | None = None,
    query_type_hint: str | None = None,
    run_key: str | None = None,
    session_id: str | None = None,
    ab_overrides: dict | None = None,
) -> RerankOutput:
    """Rerank web search results with stack-selected rerank stages."""
    if not candidates:
        record_rerank_stage(
            stage="empty",
            input_count=0,
            output_count=0,
            duration_seconds=0.0,
        )
        return RerankOutput(results=[], embedding_context=None)
    if len(candidates) <= top_k:
        logger.debug("Candidates (%s) <= top_k (%s), skipping rerank", len(candidates), top_k)
        record_rerank_stage(
            stage="bypass",
            input_count=len(candidates),
            output_count=len(candidates),
            duration_seconds=0.0,
        )
        return RerankOutput(results=candidates, embedding_context=None)

    if ab_overrides:
        if "top_k" in ab_overrides:
            top_k = int(ab_overrides["top_k"])
            logger.debug("A/B override: top_k -> %s", top_k)
        if "provider" in ab_overrides:
            logger.debug("A/B override: provider -> %s", ab_overrides["provider"])
        if "diversity_weight" in ab_overrides:
            logger.debug("A/B override: diversity_weight -> %s", ab_overrides["diversity_weight"])
        if "entity_boost" in ab_overrides:
            logger.debug("A/B override: entity_boost -> %s", ab_overrides["entity_boost"])

    instruction = _build_rerank_instruction(
        research_goal=research_goal,
        query_type_hint=query_type_hint,
    )
    instruction_length = len(instruction) if instruction else None

    decision = decide_rerank(
        query=query,
        candidate_count=len(candidates),
        top_k=top_k,
        query_type_hint=query_type_hint,
    )
    if not decision.should_rerank:
        logger.info(
            "Rerank bypassed by policy: reason=%s count=%s",
            decision.reason,
            len(candidates),
        )
        record_rerank_stage(
            stage="policy_bypass",
            input_count=len(candidates),
            output_count=len(candidates),
            duration_seconds=0.0,
        )
        emit_observability_event(
            logger,
            "rerank.bypassed",
            reason=decision.reason,
            query=query[:200],
            candidate_count=len(candidates),
        )
        return RerankOutput(results=candidates, embedding_context=None)

    original_count = len(candidates)
    pipeline_start = time.time()
    plan = build_rerank_stack_plan(settings.rerank_stack_mode)
    logger.info(
        "Starting rerank pipeline: count=%s top_k=%s mode=%s",
        original_count,
        top_k,
        plan.mode,
    )

    query_embedding: list[float] | None = None
    try:
        query_embedding = await embed_query(query, timeout=15.0)
    except (EmbeddingTimeoutError, EmbeddingAPIError, CircuitOpenError, Exception) as exc:
        logger.warning(
            "Query embedding failed: %s: %s; bi-encoder and diversity will be skipped",
            type(exc).__name__,
            exc,
        )

    embedding_ctx: RerankEmbeddingContext | None = None
    final_provider = settings.rerank_provider.strip().lower()
    final_model: str | None = None
    max_rerank_score = 0.0

    with tracer.start_as_current_span(
        "rerank.pipeline",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            "openinference.span.kind": "RERANKER",
            INPUT_VALUE: query[:500],
            INPUT_MIME_TYPE: "text/plain",
            SEARCH_QUERY: query[:500],
            RERANK_INPUT_COUNT: original_count,
            "rerank.top_k": top_k,
            "rerank.stack_mode": plan.mode,
        },
    ) as main_span:
        stage1_output_count = original_count
        if query_embedding and len(candidates) > top_k * 2:
            bi_encoder_top_k = top_k * 3
            stage1_start = time.time()
            before_bi_encoder = list(candidates)
            try:
                candidates, embedding_ctx = await bi_encoder_filter(
                    query_embedding,
                    candidates,
                    top_k=bi_encoder_top_k,
                )
                stage1_output_count = len(candidates)
            except Exception as exc:
                logger.warning(
                    "Bi-encoder filter failed: %s: %s, using top_k*2 slice",
                    type(exc).__name__,
                    exc,
                )
                candidates = candidates[:bi_encoder_top_k]
                stage1_output_count = len(candidates)
            stage1_duration = time.time() - stage1_start
            record_rerank_candidate_rows(
                logger,
                run_key=run_key,
                stage="bi_encoder",
                before_candidates=before_bi_encoder,
                after_candidates=candidates,
                payload_json={
                    "original_count": original_count,
                    "bi_encoder_top_k": bi_encoder_top_k,
                },
            )
            record_bi_encoder_stage(
                original_count=original_count,
                output_count=stage1_output_count,
                duration_seconds=stage1_duration,
                run_key=run_key,
                query_type_hint=query_type_hint,
                entity_overlap_enabled=getattr(settings, "rerank_entity_overlap_enabled", False),
                main_span=main_span,
                logger=logger,
            )

        if plan.use_cross_encoder:
            configured_provider = settings.rerank_provider.strip().lower()
            if ab_overrides and "provider" in ab_overrides:
                configured_provider = ab_overrides["provider"].strip().lower()
            cross_outcome = await run_cross_encoder_stage(
                query=query,
                candidates=candidates,
                provider_id=configured_provider,
                instruction=instruction,
                query_type_hint=query_type_hint,
                query_entities=query_entities,
                searxng_time_range=searxng_time_range,
                original_count=original_count,
                run_key=run_key,
                main_span=main_span,
                logger=logger,
                ab_entity_boost=float(ab_overrides["entity_boost"]) if ab_overrides and "entity_boost" in ab_overrides else None,
            )
            if cross_outcome.error is not None and not getattr(cross_outcome, "relevance_scores", []):
                logger.warning(
                    "All rerank providers failed; preserving merged candidate order: %s",
                    cross_outcome.error,
                )
            if getattr(cross_outcome, "relevance_scores", []):
                candidates = cross_outcome.candidates
                max_rerank_score = cross_outcome.max_score
                final_provider = cross_outcome.provider
                final_model = cross_outcome.model

        if plan.use_llm_reranker:
            llm_outcome = await run_llm_stage(
                query=query,
                candidates=candidates,
                top_k=top_k,
                candidate_limit=settings.rerank_llm_candidate_limit,
                query_type_hint=query_type_hint,
                research_goal=research_goal,
                instruction=instruction,
                query_entities=query_entities,
                searxng_time_range=searxng_time_range,
                run_key=run_key,
                session_id=session_id or run_key,
                main_span=main_span,
                logger=logger,
                ab_entity_boost=float(ab_overrides["entity_boost"]) if ab_overrides and "entity_boost" in ab_overrides else None,
            )
            if getattr(llm_outcome, "relevance_scores", []) or getattr(llm_outcome, "output_count", 0):
                candidates = llm_outcome.candidates
                max_rerank_score = llm_outcome.max_score
                final_provider = llm_outcome.provider
                final_model = llm_outcome.model

        diversity_result = await run_diversity_pruning(
            query=query,
            query_embedding=query_embedding,
            candidates=candidates,
            top_k=top_k,
            embedding_ctx=embedding_ctx,
            mmr_lambda=float(ab_overrides["diversity_weight"]) if ab_overrides and "diversity_weight" in ab_overrides else settings.mmr_lambda_param,
            logger=logger,
            run_key=run_key,
            searxng_time_range=searxng_time_range,
        )
        candidates = diversity_result.candidates

        # Update scores to reflect MMR ordering so final scores match final rank.
        # MMR reorders candidates by relevance+diversity, but candidate.score still
        # holds the pre-MMR reranker score. Assign decaying scores by MMR rank
        # position so downstream consumers (analytics, threshold filter) see
        # score-order consistency.
        for position, candidate in enumerate(candidates):
            candidates[position] = candidate.model_copy(
                update={"score": math.exp(-0.3 * position)}
            )
        record_diversity_stage(
            input_count=diversity_result.input_count,
            output_count=diversity_result.output_count,
            duration_seconds=diversity_result.duration_seconds,
            removed_count=diversity_result.removed_count,
            mmr_lambda=float(ab_overrides["diversity_weight"])
            if ab_overrides and "diversity_weight" in ab_overrides
            else settings.mmr_lambda_param,
            run_key=run_key,
            query_type_hint=query_type_hint,
            entity_overlap_enabled=getattr(
                settings, "rerank_entity_overlap_enabled", False
            ),
            main_span=main_span,
            logger=logger,
        )

        score_threshold = settings.rerank_score_threshold
        final_results = [
            result for result in candidates if result.score is None or result.score >= score_threshold
        ][:top_k]

        main_span.set_attribute("rerank.final_count", len(final_results))
        logger.info(
            "Rerank pipeline complete: %s -> %s results",
            original_count,
            len(final_results),
        )
        emit_rerank_summary(
            logger,
            query=query,
            input_count=original_count,
            output=final_results,
            top_k=top_k,
            duration_seconds=time.time() - pipeline_start,
            score_threshold=score_threshold,
            provider=final_provider,
            model=final_model,
            max_score=max_rerank_score,
            instruction_present=bool(instruction),
            instruction_length=instruction_length,
            query_type_hint=query_type_hint,
        )
        emit_observability_event(
            logger,
            "rerank.completed",
            query=query[:200],
            input_count=original_count,
            output_count=len(final_results),
            bypassed=False,
            reason="policy_eligible",
            instruction_present=bool(instruction),
            instruction_length=instruction_length,
            query_type_hint=query_type_hint,
        )
        return RerankOutput(results=final_results, embedding_context=embedding_ctx)
