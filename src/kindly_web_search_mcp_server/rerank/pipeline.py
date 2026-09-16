"""Reranking pipeline orchestration."""

from __future__ import annotations

import logging
import time
from typing import Any

from opentelemetry import trace

from ..analytics.producers import emit_observability_event
from ..analytics.rerank_telemetry import emit_rerank_summary, record_bi_encoder_stage
from ..ml import embed_query
from ..models import WebSearchResult
from ..prompts.rerank import (
    _normalize_prompt_text,
    build_rankllm_query,
    build_relevance_query,
    build_voyage_instruction,
)
from ..settings import settings
from ..telemetry import (
    INPUT_MIME_TYPE,
    INPUT_VALUE,
    RERANK_INPUT_COUNT,
    SEARCH_QUERY,
    record_rerank_stage,
)
from .bi_encoder import bi_encoder_rank, run_conditional_bi_encoder
from .cross_encoder import run_cross_encoder_stage
from .llm import run_llm_stage
from .mmr import select_mmr_slate
from .models import (
    CROSS_ENCODER_INPUT_LIMIT,
    FINAL_RESULT_LIMIT,
    RANKLLM_INPUT_LIMIT,
    RerankOutput,
    RerankOverflowItem,
    RerankOverflowStage,
    RerankStageSummary,
)

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


def _assign_terminal_scores(
    candidates: list[WebSearchResult],
    terminal_stage: str,
) -> list[WebSearchResult]:
    updated: list[WebSearchResult] = []
    for candidate in candidates:
        if candidate.final_score is not None:
            updated.append(candidate)
            continue
        if terminal_stage == "rankllm":
            scores = (
                candidate.rankllm_score,
                candidate.cross_encoder_score,
                candidate.bi_encoder_score,
                candidate.retrieval_rrf_score,
            )
        elif terminal_stage in {"cross_encoder", "mmr_fallback"}:
            scores = (
                candidate.cross_encoder_score,
                candidate.bi_encoder_score,
                candidate.retrieval_rrf_score,
            )
        elif terminal_stage == "bi_encoder":
            scores = (candidate.bi_encoder_score, candidate.retrieval_rrf_score)
        else:
            scores = (candidate.retrieval_rrf_score,)
        score = next((value for value in scores if value is not None), candidate.raw_score)
        updated.append(
            candidate.model_copy(update={"final_score": 0.0 if score is None else score})
        )
    return updated


def _canonical_keys(candidates: list[WebSearchResult]) -> set[str]:
    from ..utils.url_canonicalize import canonicalize_url

    return {canonicalize_url(candidate.link) for candidate in candidates if candidate.link}


def _build_overflow_items(
    *,
    final_results: list[WebSearchResult],
    merged: list[WebSearchResult],
    cross_full: list[WebSearchResult],
    cross_window: list[WebSearchResult],
    llm_full: list[WebSearchResult],
    rankllm_success: bool,
    mmr_applied: bool,
) -> list[RerankOverflowItem]:
    from ..utils.url_canonicalize import canonicalize_url

    final_keys = _canonical_keys(final_results)
    seen = set(final_keys)
    overflow: list[RerankOverflowItem] = []

    def add(stage: RerankOverflowStage, candidates: list[WebSearchResult]) -> None:
        for candidate in candidates:
            if not candidate.link:
                continue
            key = canonicalize_url(candidate.link)
            if key in seen:
                continue
            seen.add(key)
            overflow.append(RerankOverflowItem(stage=stage, result=candidate))

    if rankllm_success:
        add("rankllm", llm_full)
    elif mmr_applied:
        add("mmr_fallback", cross_window)
    else:
        add("cross", cross_full)

    if rankllm_success or mmr_applied:
        window_keys = _canonical_keys(cross_window)
        add(
            "cross",
            [
                candidate
                for candidate in cross_full
                if canonicalize_url(candidate.link) not in window_keys
            ],
        )
    add("rrf", merged)
    return overflow


def _score_for_mmr(candidate: WebSearchResult) -> float:
    for score in (
        candidate.cross_encoder_score,
        candidate.final_score,
        candidate.retrieval_rrf_score,
        candidate.bi_encoder_score,
    ):
        if score is not None:
            return float(score)
    raise ValueError(f"candidate {candidate.link!r} has no relevance score for MMR")


async def rerank_results(
    query: str,
    candidates: list[WebSearchResult],
    *,
    research_goal: str,
    query_type_hint: str | None = None,
    run_key: str | None = None,
    session_id: str | None = None,
    precomputed_embedding: list[float] | None = None,
    reranking_instructions: str | None = None,
    top_k: int | None = None,
) -> RerankOutput:
    """Apply the bounded bi, cross, RankLLM, and MMR funnel."""
    if not research_goal or not research_goal.strip():
        raise ValueError("research_goal must be non-blank")
    if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 0):
        raise ValueError("top_k must be a non-negative integer")
    final_limit = FINAL_RESULT_LIMIT if top_k is None else min(top_k, FINAL_RESULT_LIMIT)
    if not candidates:
        record_rerank_stage(stage="empty", input_count=0, output_count=0, duration_seconds=0.0)
        return RerankOutput(results=[], embedding_context=None, provider=None, model=None)

    relevance_query = build_relevance_query(query, research_goal)
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
        bi_start = time.monotonic()
        bi_outcome = await run_conditional_bi_encoder(
            relevance_query,
            list(candidates),
            precomputed_embedding=precomputed_embedding,
            logger=logger,
        )
        bi_candidates = list(bi_outcome.candidates[:CROSS_ENCODER_INPUT_LIMIT])
        bi_duration = (time.monotonic() - bi_start) * 1000.0
        bi_applied = bi_outcome.status == "applied"
        bi_status = (
            "success"
            if bi_applied
            else "skipped"
            if original_count <= CROSS_ENCODER_INPUT_LIMIT
            else "failed_open"
        )
        bi_scores = [
            float(candidate.bi_encoder_score)
            for candidate in bi_candidates
            if candidate.bi_encoder_score is not None
        ]
        stage_summaries.append(
            RerankStageSummary(
                stage="bi_encoder",
                input_count=original_count,
                output_count=len(bi_candidates),
                duration_ms=bi_duration,
                status=bi_status,
                error_type=bi_outcome.status if bi_status == "failed_open" else None,
                max_score=max(bi_scores) if bi_scores else None,
                avg_score=sum(bi_scores) / len(bi_scores) if bi_scores else None,
                instruction_present=bool(reranking_instructions),
                instruction_length=(
                    len(reranking_instructions) if reranking_instructions else None
                ),
                query_type_hint=query_type_hint,
            )
        )
        funnel_counts["bi_output_count"] = len(bi_candidates)
        record_bi_encoder_stage(
            original_count=original_count,
            output_count=len(bi_candidates),
            duration_seconds=bi_duration / 1000.0,
            main_span=main_span,
        )

        cross_start = time.monotonic()
        cross_outcome = await run_cross_encoder_stage(
            query=query,
            instruction=build_voyage_instruction(
                query_type_hint,
                research_goal,
                reranking_instructions=reranking_instructions,
            ),
            candidates=bi_candidates,
            query_type_hint=query_type_hint,
            original_count=original_count,
            run_key=run_key,
            main_span=main_span,
            logger=logger,
            output_limit=RANKLLM_INPUT_LIMIT,
        )
        cross_candidates = list(cross_outcome.candidates[:RANKLLM_INPUT_LIMIT])
        cross_full = list(cross_outcome.full_candidates or cross_candidates)
        cross_duration = (time.monotonic() - cross_start) * 1000.0
        cross_success = cross_outcome.error is None and bool(cross_outcome.relevance_scores)
        cross_status = "success" if cross_success else "failed_open"
        stage_summaries.append(
            RerankStageSummary(
                stage="cross_encoder",
                provider=cross_outcome.provider,
                model=cross_outcome.model,
                input_count=len(bi_candidates),
                output_count=len(cross_candidates),
                duration_ms=cross_duration,
                status=cross_status,
                error_type=(type(cross_outcome.error).__name__ if cross_outcome.error else None),
                max_score=cross_outcome.max_score if cross_success else None,
                avg_score=cross_outcome.avg_score if cross_success else None,
                instruction_present=bool(reranking_instructions),
                instruction_length=(
                    len(reranking_instructions) if reranking_instructions else None
                ),
                query_type_hint=query_type_hint,
            )
        )
        funnel_counts["cross_output_count"] = len(cross_candidates)

        rankllm_query = build_rankllm_query(
            query,
            research_goal,
            query_type_hint,
            reranking_instructions=reranking_instructions,
        )
        normalized_caller = _normalize_prompt_text(reranking_instructions, cap=500)
        llm_outcome = None
        llm_candidates: list[WebSearchResult] = []
        llm_full: list[WebSearchResult] = []
        rankllm_success = False
        llm_start = time.monotonic()
        if settings.rankllm_enabled:
            rankllm_query = build_rankllm_query(
                query,
                research_goal,
                query_type_hint,
                reranking_instructions=reranking_instructions,
            )
            llm_outcome = await run_llm_stage(
                query=rankllm_query,
                candidates=cross_candidates,
                request_id=session_id or run_key,
                query_type_hint=query_type_hint,
                run_key=run_key,
                main_span=main_span,
                logger=logger,
            )
            llm_candidates = list(llm_outcome.candidates[:FINAL_RESULT_LIMIT])
            llm_full = list(llm_outcome.full_candidates or llm_candidates)
            rankllm_success = bool(llm_outcome.relevance_scores) and llm_outcome.valid_passes > 0
        llm_duration = (time.monotonic() - llm_start) * 1000.0
        if llm_outcome is not None:
            llm_status = (
                "partial"
                if rankllm_success and llm_outcome.failed_passes
                else "success"
                if rankllm_success and llm_outcome.provider == "google"
                else "fallback_success"
                if rankllm_success
                else "failed_open"
            )
            stage_summaries.append(
                RerankStageSummary(
                    stage="rankllm",
                    provider=llm_outcome.provider,
                    model=llm_outcome.model,
                    input_count=len(cross_candidates),
                    output_count=len(llm_candidates),
                    duration_ms=llm_duration,
                    status=llm_status,
                    error_type=(type(llm_outcome.error).__name__ if llm_outcome.error else None),
                    max_score=llm_outcome.max_score if rankllm_success else None,
                    avg_score=llm_outcome.avg_score if rankllm_success else None,
                    input_tokens=llm_outcome.input_tokens,
                    output_tokens=llm_outcome.output_tokens,
                    instruction_present=bool(normalized_caller),
                    instruction_length=len(normalized_caller) if normalized_caller else None,
                    query_type_hint=query_type_hint,
                    attempted_passes=llm_outcome.attempted_passes or None,
                    valid_passes=llm_outcome.valid_passes or None,
                    failed_passes=llm_outcome.failed_passes or None,
                )
            )
        else:
            stage_summaries.append(
                RerankStageSummary(
                    stage="rankllm",
                    input_count=len(cross_candidates),
                    output_count=0,
                    duration_ms=0.0,
                    status="skipped",
                    query_type_hint=query_type_hint,
                )
            )
        funnel_counts["rankllm_output_count"] = len(llm_candidates)
        # The status flag does not narrow llm_outcome for a checker, so name both.
        llm_success = rankllm_success and llm_outcome is not None
        final_provider = llm_outcome.provider if llm_success else None
        final_model = llm_outcome.model if llm_success else None
        terminal_stage = (
            "rankllm"
            if rankllm_success
            else "cross_encoder"
            if cross_success
            else "bi_encoder"
            if bi_applied
            else "rrf"
        )
        final_results = llm_candidates if rankllm_success else list(cross_candidates)
        embedding_context = bi_outcome.embedding_context
        mmr_applied = False
        mmr_error: Exception | None = None
        mmr_started = time.monotonic()

        if not rankllm_success and final_limit > 0 and len(cross_candidates) > final_limit:
            try:
                slate_embeddings: list[list[float]] = []
                if embedding_context is not None:
                    for candidate in cross_candidates:
                        embedded = embedding_context.find(candidate.link)
                        if embedded is None:
                            slate_embeddings = []
                            break
                        slate_embeddings.append(list(embedded.dense))
                if len(slate_embeddings) != len(cross_candidates):
                    query_embedding = precomputed_embedding or await embed_query(
                        relevance_query,
                        timeout=settings.rerank_bi_encoder_timeout_seconds,
                    )
                    _, fallback_context = await bi_encoder_rank(
                        query_embedding,
                        cross_candidates,
                    )
                    if fallback_context is None:
                        raise ValueError("MMR candidate embeddings unavailable")
                    slate_embeddings = [
                        list(embedding.dense)
                        for candidate in cross_candidates
                        for embedding in [fallback_context.find(candidate.link)]
                        if embedding is not None
                    ]
                if len(slate_embeddings) != len(cross_candidates):
                    raise ValueError("MMR candidate embedding count mismatch")
                slate = select_mmr_slate(
                    slate_embeddings,
                    [candidate.link for candidate in cross_candidates],
                    output_size=min(final_limit, len(cross_candidates)),
                    relevance_scores=[_score_for_mmr(candidate) for candidate in cross_candidates],
                )
                selected_count = min(final_limit, len(cross_candidates))
                selected_indices = slate.selected_indices[:selected_count]
                final_results = [
                    cross_candidates[index].model_copy(
                        update={"diversity_penalty": slate.diversity_penalties[index]}
                    )
                    for index in selected_indices
                ]
                mmr_applied = True
                terminal_stage = "mmr_fallback"
                stage_summaries.append(
                    RerankStageSummary(
                        stage="mmr_fallback",
                        input_count=len(cross_candidates),
                        output_count=len(final_results),
                        duration_ms=(time.monotonic() - mmr_started) * 1000.0,
                        status="success",
                        max_score=None,
                        avg_score=None,
                    )
                )
            except Exception as exc:
                mmr_error = exc
                logger.warning("MMR fallback failed open: %s: %s", type(exc).__name__, exc)

        if not mmr_applied:
            final_results = final_results[:final_limit]
            stage_summaries.append(
                RerankStageSummary(
                    stage="mmr_fallback",
                    input_count=len(cross_candidates) if not rankllm_success else 0,
                    output_count=0,
                    duration_ms=(time.monotonic() - mmr_started) * 1000.0,
                    status="failed_open" if mmr_error else "skipped",
                    error_type=type(mmr_error).__name__ if mmr_error else None,
                )
            )
            if mmr_error is not None:
                terminal_stage = (
                    "cross_encoder" if cross_success else "bi_encoder" if bi_applied else "rrf"
                )

        if not rankllm_success:
            final_provider = cross_outcome.provider if cross_success else None
            final_model = cross_outcome.model if cross_success else None
        final_results = _assign_terminal_scores(final_results, terminal_stage)
        overflow_items = _build_overflow_items(
            final_results=final_results,
            merged=list(candidates),
            cross_full=cross_full,
            cross_window=cross_candidates,
            llm_full=llm_full,
            rankllm_success=rankllm_success,
            mmr_applied=mmr_applied,
        )
        funnel_counts["final_output_count"] = len(final_results)
        funnel_counts["overflow_count"] = len(overflow_items)
        main_span.set_attribute("rerank.final_count", len(final_results))
        duration_seconds = time.monotonic() - pipeline_started
        emit_rerank_summary(
            logger,
            query=query,
            input_count=original_count,
            output=final_results,
            top_k=final_limit,
            duration_seconds=duration_seconds,
            max_score=(
                max((candidate.final_score or 0.0 for candidate in final_results), default=0.0)
                if final_results
                else None
            ),
            provider=final_provider,
            model=final_model,
            terminal_stage=terminal_stage,
        )
        emit_observability_event(
            logger,
            "rerank.completed",
            query=query[:200],
            input_count=original_count,
            output_count=len(final_results),
            bypassed=False,
            terminal_stage=terminal_stage,
            query_type_hint=query_type_hint,
        )
        return RerankOutput(
            results=final_results,
            embedding_context=embedding_context,
            provider=final_provider,
            model=final_model,
            stage_summaries=stage_summaries,
            funnel_counts=funnel_counts,
            overflow_items=overflow_items,
            terminal_stage=terminal_stage,
        )
