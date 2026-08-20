"""Execution helpers for cross-encoder and RankLLM stages."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from statistics import median
import time
from typing import Any

from ..models import WebSearchResult
from .llm_rerank import rerank_with_llm
from .limits import RANKLLM_INPUT_LIMIT
from .observability import record_rerank_candidate_rows_async
from .providers import rerank_with_provider_fallback
from .reporting import record_ranked_stage
from .stages import apply_ranked_results
from ..settings import settings


@dataclass(frozen=True, slots=True)
class RankedStageOutcome:
    candidates: list[WebSearchResult]
    provider: str
    model: str | None
    stage_name: str
    input_count: int
    output_count: int
    duration_seconds: float
    relevance_scores: list[float]
    max_score: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: Exception | None = None


async def _apply_ranked_stage(
    *,
    stage_name: str,
    provider: str,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    input_candidates: list[WebSearchResult],
    ranked_results: list[Any],
    duration_seconds: float,
    query_type_hint: str | None,
    payload_json: dict[str, Any],
    run_key: str | None,
    main_span: Any,
    logger: logging.Logger,
    store_cross_scores: bool = False,
    output_limit: int | None = None,
) -> RankedStageOutcome:
    before_candidates = [candidate.model_copy() for candidate in input_candidates]
    candidates, relevance_scores, _, _ = apply_ranked_results(
        list(input_candidates),
        ranked_results,
        preserve_raw_scores=True,
        store_cross_scores=store_cross_scores,
        update_score=True,
        recency_weight=(
            settings.rerank_recency_weight if stage_name == "cross_encoder" else 0.0
        ),
        half_life_days=settings.rerank_recency_half_life_days,
    )
    # apply_ranked_results preserves the unranked tail in incoming order.

    cross_encoder_scores = None
    if store_cross_scores:
        cross_encoder_scores = {
            c.link: getattr(c, "cross_relevance_score", 0.0)
            for c in candidates
            if getattr(c, "cross_relevance_score", None) is not None
        }

    sliced_candidates = candidates[:output_limit] if output_limit is not None else candidates

    await record_rerank_candidate_rows_async(
        logger,
        run_key=run_key,
        stage=stage_name,
        before_candidates=before_candidates,
        after_candidates=sliced_candidates,
        payload_json=payload_json,
        cross_encoder_scores=cross_encoder_scores,
    )
    max_score, _ = record_ranked_stage(
        stage_name=stage_name,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_count=len(input_candidates),
        output_count=len(sliced_candidates),
        duration_seconds=duration_seconds,
        relevance_scores=relevance_scores if stage_name != "rankllm" else [],
        payload_json=payload_json,
        query_type_hint=query_type_hint,
        entity_overlap_enabled=False,
        run_key=run_key,
        main_span=main_span,
        logger=logger,
    )
    return RankedStageOutcome(
        candidates=sliced_candidates,
        provider=provider,
        model=model,
        stage_name=stage_name,
        input_count=len(input_candidates),
        output_count=len(sliced_candidates),
        duration_seconds=duration_seconds,
        relevance_scores=relevance_scores if stage_name != "rankllm" else [],
        max_score=float(max_score) if (stage_name != "rankllm" and max_score is not None) else 0.0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


async def run_cross_encoder_stage(
    *,
    query: str,
    candidates: list[WebSearchResult],
    query_type_hint: str | None,
    original_count: int,
    run_key: str | None,
    main_span: Any,
    logger: logging.Logger,
    output_limit: int = RANKLLM_INPUT_LIMIT,
) -> RankedStageOutcome:
    stage_start = time.monotonic()
    outcome = await rerank_with_provider_fallback(query, candidates)
    duration_seconds = time.monotonic() - stage_start
    if not outcome.ranked:
        sliced_candidates = candidates[:output_limit]
        return RankedStageOutcome(
            candidates=sliced_candidates,
            provider=outcome.provider_id or "chain_failed",
            model=outcome.model,
            stage_name="cross_encoder",
            input_count=len(candidates),
            output_count=len(sliced_candidates),
            duration_seconds=duration_seconds,
            relevance_scores=[],
            max_score=0.0,
            error=outcome.error,
        )

    raw_scores = [float(result.score) for result in outcome.ranked]
    payload_json = {
        "original_count": original_count,
        "document_format": "ordered_yaml_v1",
        "input_count": len(candidates),
        "top_n": len(candidates),
        "raw_score_min": min(raw_scores),
        "raw_score_median": median(raw_scores),
        "raw_score_max": max(raw_scores),
    }
    return await _apply_ranked_stage(
        stage_name="cross_encoder",
        provider=outcome.provider_id,
        model=outcome.model,
        input_tokens=None,
        output_tokens=None,
        input_candidates=candidates,
        ranked_results=outcome.ranked,
        duration_seconds=duration_seconds,
        query_type_hint=query_type_hint,
        payload_json=payload_json,
        run_key=run_key,
        main_span=main_span,
        logger=logger,
        store_cross_scores=True,
        output_limit=output_limit,
    )


async def run_llm_stage(
    *,
    query: str,
    candidates: list[WebSearchResult],
    request_id: str | None,
    query_type_hint: str | None,
    run_key: str | None,
    main_span: Any,
    logger: logging.Logger,
) -> RankedStageOutcome:
    stage_start = time.monotonic()
    try:
        outcome = await rerank_with_llm(query, candidates, request_id=request_id)
    except Exception as exc:
        outcome = None
        error = exc
    else:
        error = outcome.error
    duration_seconds = time.monotonic() - stage_start

    if outcome is None or not outcome.ranked:
        sliced_candidates = candidates[:15]
        return RankedStageOutcome(
            candidates=sliced_candidates,
            provider=outcome.endpoint_name if outcome else "chain_failed",
            model=outcome.model if outcome else None,
            stage_name="rankllm",
            input_count=len(candidates),
            output_count=len(sliced_candidates),
            duration_seconds=duration_seconds,
            relevance_scores=[],
            max_score=0.0,
            input_tokens=outcome.input_tokens if outcome else None,
            output_tokens=outcome.output_tokens if outcome else None,
            error=error,
        )

    payload_json = {
        "original_count": len(candidates),
        "llm_candidate_limit": len(candidates),
        "rankllm_provider": outcome.endpoint_name,
        "rankllm_model": outcome.model,
    }
    return await _apply_ranked_stage(
        stage_name="rankllm",
        provider=outcome.endpoint_name,
        model=outcome.model,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        input_candidates=candidates,
        ranked_results=outcome.ranked,
        duration_seconds=duration_seconds,
        query_type_hint=query_type_hint,
        payload_json=payload_json,
        run_key=run_key,
        main_span=main_span,
        logger=logger,
        output_limit=15,
    )
