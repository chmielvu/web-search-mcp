"""Core reranking orchestration pipeline."""

from __future__ import annotations

import logging
import time
from typing import Any

from opentelemetry import trace

from ..models import WebSearchResult
from ..prompts.rerank import build_cross_encoder_query
from ..telemetry import INPUT_MIME_TYPE, INPUT_VALUE, RERANK_INPUT_COUNT, SEARCH_QUERY
from ..telemetry import record_rerank_stage
from ..utils.observability import emit_observability_event
from .conditional_bi import run_conditional_bi_encoder
from .models import RerankOutput, RerankStageSummary
from .observability import emit_rerank_summary
from .reporting import record_bi_encoder_stage
from .stage_runner import run_cross_encoder_stage, run_llm_stage

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


async def rerank_results(
    query: str,
    candidates: list[WebSearchResult],
    *,
    research_goal: str,
    query_type_hint: str | None = None,
    run_key: str | None = None,
    session_id: str | None = None,
    precomputed_embedding: list[float] | None = None,
) -> RerankOutput:
    """Apply conditional bi, cross, and RankLLM funnel stages."""
    if not research_goal or not research_goal.strip():
        raise ValueError("research_goal must be non-blank")
    if not candidates:
        record_rerank_stage(stage="empty", input_count=0, output_count=0, duration_seconds=0.0)
        return RerankOutput(results=[], embedding_context=None, provider=None, model=None)

    original_count = len(candidates)
    pipeline_started = time.monotonic()
    stage_summaries: list[RerankStageSummary] = []
    funnel_counts: dict[str, int] = {"input_count": original_count}

    with tracer.start_as_current_span(
        "rerank.pipeline",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            "openinference.span.kind": "RERANKER",
            INPUT_VALUE: query[:500],
            INPUT_MIME_TYPE: "text/plain",
            SEARCH_QUERY: query[:500],
            RERANK_INPUT_COUNT: original_count,
        },
    ) as main_span:
        # 1. Bi-encoder Stage (truncates to 100)
        bi_start = time.monotonic()
        bi_outcome = await run_conditional_bi_encoder(
            query,
            candidates,
            precomputed_embedding=precomputed_embedding,
            logger=logger,
        )
        bi_candidates = bi_outcome.candidates
        bi_duration = (time.monotonic() - bi_start) * 1000.0

        bi_status = "skipped"
        if original_count > 100:
            bi_status = "success" if bi_outcome.status == "applied" else "failed_open"

        bi_summary = RerankStageSummary(
            stage="bi_encoder",
            provider=None,
            model=None,
            input_count=original_count,
            output_count=len(bi_candidates),
            duration_ms=bi_duration,
            status=bi_status,
            error_type=bi_outcome.status if bi_status == "failed_open" else None,
            max_score=None,
            avg_score=None,
            payload_json={"status": bi_outcome.status},
        )
        stage_summaries.append(bi_summary)
        funnel_counts["bi_output_count"] = len(bi_candidates)

        record_bi_encoder_stage(
            original_count=original_count,
            output_count=len(bi_candidates),
            duration_seconds=bi_duration / 1000.0,
            run_key=run_key,
            query_type_hint=query_type_hint,
            entity_overlap_enabled=False,
            main_span=main_span,
            logger=logger,
            payload_json={"status": bi_outcome.status},
        )

        # 2. Cross-encoder Stage (ranks and truncates to 30)
        cross_start = time.monotonic()
        cross_outcome = await run_cross_encoder_stage(
            query=build_cross_encoder_query(query, query_type_hint, research_goal),
            candidates=bi_candidates,
            query_type_hint=query_type_hint,
            original_count=original_count,
            run_key=run_key,
            main_span=main_span,
            logger=logger,
        )
        cross_candidates = cross_outcome.candidates
        cross_duration = (time.monotonic() - cross_start) * 1000.0

        cross_status = "failed_open"
        if cross_outcome.error is None:
            cross_status = (
                "success" if cross_outcome.provider == "cohere_fast" else "fallback_success"
            )

        cross_max_score = (
            max(cross_outcome.relevance_scores) if cross_outcome.relevance_scores else None
        )
        cross_avg_score = (
            sum(cross_outcome.relevance_scores) / len(cross_outcome.relevance_scores)
            if cross_outcome.relevance_scores
            else None
        )

        cross_summary = RerankStageSummary(
            stage="cross_encoder",
            provider=cross_outcome.provider,
            model=cross_outcome.model,
            input_count=len(bi_candidates),
            output_count=len(cross_candidates),
            duration_ms=cross_duration,
            status=cross_status,
            error_type=type(cross_outcome.error).__name__ if cross_outcome.error else None,
            max_score=cross_max_score,
            avg_score=cross_avg_score,
            payload_json={},
        )
        stage_summaries.append(cross_summary)
        funnel_counts["cross_output_count"] = len(cross_candidates)

        # 3. RankLLM Stage (ranks and truncates to 15)
        llm_start = time.monotonic()
        llm_outcome = await run_llm_stage(
            query=query,
            candidates=cross_candidates,
            request_id=session_id or run_key,
            query_type_hint=query_type_hint,
            run_key=run_key,
            main_span=main_span,
            logger=logger,
        )
        llm_candidates = llm_outcome.candidates
        llm_duration = (time.monotonic() - llm_start) * 1000.0

        llm_status = "failed_open"
        if llm_outcome.error is None:
            llm_status = "success" if llm_outcome.provider == "gemini" else "fallback_success"

        llm_summary = RerankStageSummary(
            stage="rankllm",
            provider=llm_outcome.provider,
            model=llm_outcome.model,
            input_count=len(cross_candidates),
            output_count=len(llm_candidates),
            duration_ms=llm_duration,
            status=llm_status,
            error_type=type(llm_outcome.error).__name__ if llm_outcome.error else None,
            max_score=None,
            avg_score=None,
            input_tokens=llm_outcome.input_tokens,
            output_tokens=llm_outcome.output_tokens,
            payload_json={},
        )
        stage_summaries.append(llm_summary)
        funnel_counts["rankllm_output_count"] = len(llm_candidates)

        # Establish final provider and model based on the RankLLM outcome
        if llm_status in ("success", "fallback_success"):
            final_provider = llm_outcome.provider
            final_model = llm_outcome.model
        else:
            final_provider = cross_outcome.provider
            final_model = cross_outcome.model

        # Assert monotone funnel count invariant
        assert (
            original_count >= len(bi_candidates) >= len(cross_candidates) >= len(llm_candidates)
        ), (
            f"Funnel cardinalities drift: {original_count} >= {len(bi_candidates)} >= {len(cross_candidates)} >= {len(llm_candidates)}"
        )

        main_span.set_attribute("rerank.final_count", len(llm_candidates))
        duration_seconds = time.monotonic() - pipeline_started

        emit_rerank_summary(
            logger,
            query=query,
            input_count=original_count,
            output=llm_candidates,
            top_k=15,
            duration_seconds=duration_seconds,
            score_threshold=0.0,
            provider=final_provider,
            model=final_model or "",
            max_score=cross_max_score or 0.0,
            instruction_present=False,
            instruction_length=None,
            query_type_hint=query_type_hint,
        )
        emit_observability_event(
            logger,
            "rerank.completed",
            query=query[:200],
            input_count=original_count,
            output_count=len(llm_candidates),
            bypassed=False,
            query_type_hint=query_type_hint,
        )
        return RerankOutput(
            results=llm_candidates,
            embedding_context=bi_outcome.embedding_context,
            provider=final_provider,
            model=final_model,
            stage_summaries=tuple(stage_summaries),
            funnel_counts=funnel_counts,
        )
