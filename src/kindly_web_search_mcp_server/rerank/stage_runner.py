"""Execution helpers for cross-encoder and RankLLM stages."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any, Literal

from ..models import WebSearchResult
from ..settings import settings
from ..utils.url_canonicalize import canonicalize_url
from .llm_rerank import rerank_with_llm
from .limits import FINAL_RESULT_LIMIT, RANKLLM_INPUT_LIMIT
from .observability import record_rerank_candidate_rows_async
from .providers import rerank_with_provider_fallback
from .reporting import record_ranked_stage
from .stages import apply_ranked_results


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
    avg_score: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: Exception | None = None
    full_candidates: list[WebSearchResult] | None = None
    attempted_passes: int = 0
    valid_passes: int = 0
    failed_passes: int = 0


def _failed_stage(
    *,
    stage_name: str,
    provider: str,
    model: str | None,
    candidates: list[WebSearchResult],
    output_limit: int,
    duration_seconds: float,
    error: Exception | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    attempted_passes: int = 0,
    valid_passes: int = 0,
    failed_passes: int = 0,
) -> RankedStageOutcome:
    sliced_candidates = list(candidates[:output_limit])
    return RankedStageOutcome(
        candidates=sliced_candidates,
        provider=provider,
        model=model,
        stage_name=stage_name,
        input_count=len(candidates),
        output_count=len(sliced_candidates),
        duration_seconds=duration_seconds,
        relevance_scores=[],
        max_score=0.0,
        avg_score=0.0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error=error,
        full_candidates=list(candidates),
        attempted_passes=attempted_passes,
        valid_passes=valid_passes,
        failed_passes=failed_passes,
    )


async def _apply_ranked_stage(
    *,
    stage_name: Literal["cross_encoder", "rankllm"],
    provider: str,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    input_candidates: list[WebSearchResult],
    ranked_results: list[Any],
    duration_seconds: float,
    run_key: str | None,
    main_span: Any,
    logger: logging.Logger,
    output_limit: int | None = None,
    error: Exception | None = None,
    attempted_passes: int = 0,
    valid_passes: int = 0,
    failed_passes: int = 0,
) -> RankedStageOutcome:
    before_candidates = [candidate.model_copy() for candidate in input_candidates]
    candidates, relevance_scores, max_score, avg_score = apply_ranked_results(
        list(input_candidates),
        ranked_results,
        stage_name=stage_name,
        recency_weight=(settings.rerank_recency_weight if stage_name == "cross_encoder" else 0.0),
        half_life_days=settings.rerank_recency_half_life_days,
    )
    cross_encoder_scores = None
    if stage_name == "cross_encoder":
        cross_encoder_scores = {
            canonicalize_url(candidate.link): float(candidate.cross_encoder_score)
            for candidate in candidates
            if candidate.cross_encoder_score is not None
        }

    full_candidates = list(candidates)
    sliced_candidates = (
        full_candidates[:output_limit] if output_limit is not None else full_candidates
    )
    await record_rerank_candidate_rows_async(
        logger,
        run_key=run_key,
        stage=stage_name,
        before_candidates=before_candidates,
        after_candidates=sliced_candidates,
        cross_encoder_scores=cross_encoder_scores,
    )
    record_ranked_stage(
        stage_name=stage_name,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_count=len(input_candidates),
        output_count=len(sliced_candidates),
        duration_seconds=duration_seconds,
        relevance_scores=relevance_scores,
        attempted_passes=attempted_passes,
        valid_passes=valid_passes,
        failed_passes=failed_passes,
        main_span=main_span,
    )
    return RankedStageOutcome(
        candidates=sliced_candidates,
        provider=provider,
        model=model,
        stage_name=stage_name,
        input_count=len(input_candidates),
        output_count=len(sliced_candidates),
        duration_seconds=duration_seconds,
        relevance_scores=relevance_scores,
        max_score=float(max_score),
        avg_score=float(avg_score),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error=error,
        full_candidates=full_candidates,
        attempted_passes=attempted_passes,
        valid_passes=valid_passes,
        failed_passes=failed_passes,
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
    del query_type_hint, original_count
    stage_start = time.monotonic()
    outcome = await rerank_with_provider_fallback(query, candidates)
    duration_seconds = time.monotonic() - stage_start
    if not outcome.ranked:
        return _failed_stage(
            stage_name="cross_encoder",
            provider=outcome.provider_id or "chain_failed",
            model=outcome.model,
            candidates=candidates,
            output_limit=output_limit,
            duration_seconds=duration_seconds,
            error=outcome.error,
        )

    try:
        return await _apply_ranked_stage(
            stage_name="cross_encoder",
            provider=outcome.provider_id,
            model=outcome.model,
            input_tokens=None,
            output_tokens=None,
            input_candidates=candidates,
            ranked_results=outcome.ranked,
            duration_seconds=duration_seconds,
            run_key=run_key,
            main_span=main_span,
            logger=logger,
            output_limit=output_limit,
        )
    except (TypeError, ValueError) as exc:
        return _failed_stage(
            stage_name="cross_encoder",
            provider=outcome.provider_id,
            model=outcome.model,
            candidates=candidates,
            output_limit=output_limit,
            duration_seconds=duration_seconds,
            error=exc,
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
    del query_type_hint
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
        return _failed_stage(
            stage_name="rankllm",
            provider=outcome.endpoint_name if outcome else "chain_failed",
            model=outcome.model if outcome else None,
            candidates=candidates,
            output_limit=FINAL_RESULT_LIMIT,
            duration_seconds=duration_seconds,
            error=error,
            input_tokens=outcome.input_tokens if outcome else None,
            output_tokens=outcome.output_tokens if outcome else None,
            attempted_passes=outcome.attempted_passes if outcome else 0,
            valid_passes=outcome.valid_passes if outcome else 0,
            failed_passes=outcome.failed_passes if outcome else 0,
        )

    try:
        return await _apply_ranked_stage(
            stage_name="rankllm",
            provider=outcome.endpoint_name,
            model=outcome.model,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            input_candidates=candidates,
            ranked_results=outcome.ranked,
            duration_seconds=duration_seconds,
            run_key=run_key,
            main_span=main_span,
            logger=logger,
            output_limit=FINAL_RESULT_LIMIT,
            error=error,
            attempted_passes=outcome.attempted_passes,
            valid_passes=outcome.valid_passes,
            failed_passes=outcome.failed_passes,
        )
    except (TypeError, ValueError) as exc:
        return _failed_stage(
            stage_name="rankllm",
            provider=outcome.endpoint_name,
            model=outcome.model,
            candidates=candidates,
            output_limit=FINAL_RESULT_LIMIT,
            duration_seconds=duration_seconds,
            error=exc,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            attempted_passes=outcome.attempted_passes,
            valid_passes=outcome.valid_passes,
            failed_passes=outcome.failed_passes,
        )
