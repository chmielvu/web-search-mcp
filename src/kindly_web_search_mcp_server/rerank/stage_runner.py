"""Execution helpers for ranked rerank stages."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time

from ..models import WebSearchResult
from ..settings import settings
from .engines import rerank_with_engine_fallback
from .llm_rerank import rerank_with_llm
from .reporting import record_ranked_stage
from .stages import apply_entity_overlap_boost, apply_ranked_results


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


def _apply_ranked_stage(
    *,
    stage_name: str,
    provider: str,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    input_candidates: list[WebSearchResult],
    ranked_results: list,
    duration_seconds: float,
    query_type_hint: str | None,
    query_entities: list | None,
    searxng_time_range: str | None,
    payload_json: dict,
    run_key: str | None,
    main_span,
    logger: logging.Logger,
    preserve_raw_scores: bool = False,
) -> RankedStageOutcome:
    candidates, relevance_scores, _, _ = apply_ranked_results(
        input_candidates,
        ranked_results,
        preserve_raw_scores=preserve_raw_scores,
        recency_weight=settings.rerank_recency_weight,
        half_life_days=settings.rerank_recency_half_life_days,
        searxng_time_range=searxng_time_range,
    )
    apply_entity_overlap_boost(
        candidates,
        query_entities=query_entities,
        entity_overlap_enabled=getattr(settings, "rerank_entity_overlap_enabled", False),
        entity_overlap_weight=getattr(settings, "rerank_entity_overlap_weight", 0.15),
        logger=logger,
        ab_weight=float(payload_json.get("entity_boost")) if payload_json.get("entity_boost") is not None else None,
    )
    relevance_scores = [
        candidate.score for candidate in candidates[: min(10, len(candidates))] if candidate.score is not None
    ]
    max_score, _ = record_ranked_stage(
        stage_name=stage_name,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_count=len(input_candidates),
        output_count=len(candidates),
        duration_seconds=duration_seconds,
        relevance_scores=relevance_scores,
        payload_json=payload_json,
        query_type_hint=query_type_hint,
        entity_overlap_enabled=getattr(settings, "rerank_entity_overlap_enabled", False),
        run_key=run_key,
        main_span=main_span,
        logger=logger,
    )
    return RankedStageOutcome(
        candidates=candidates,
        provider=provider,
        model=model,
        stage_name=stage_name,
        input_count=len(input_candidates),
        output_count=len(candidates),
        duration_seconds=duration_seconds,
        relevance_scores=relevance_scores,
        max_score=max_score,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


async def run_cross_encoder_stage(
    *,
    query: str,
    candidates: list[WebSearchResult],
    provider_id: str,
    instruction: str | None,
    query_type_hint: str | None,
    query_entities: list | None,
    searxng_time_range: str | None,
    original_count: int,
    run_key: str | None,
    main_span,
    logger: logging.Logger,
    ab_entity_boost: float | None = None,
    entity_boost: float = 0.15,
) -> RankedStageOutcome | None:
    stage_start = time.time()
    outcome = await rerank_with_engine_fallback(
        query,
        candidates,
        engine_id=provider_id,
        instruction=instruction,
    )
    duration_seconds = time.time() - stage_start
    if not outcome.ranked:
        return RankedStageOutcome(
            candidates=candidates,
            provider=outcome.engine_id,
            model=outcome.model,
            stage_name=outcome.engine_id,
            input_count=len(candidates),
            output_count=len(candidates),
            duration_seconds=duration_seconds,
            relevance_scores=[],
            max_score=0.0,
            input_tokens=None,
            output_tokens=None,
            error=outcome.error,
        )
    payload_json = {
        "original_count": original_count,
        "recency_weight": settings.rerank_recency_weight,
        "apply_recency": searxng_time_range is None and settings.rerank_recency_weight > 0,
        "entity_boost": ab_entity_boost,
    }
    return _apply_ranked_stage(
        stage_name=outcome.engine_id,
        provider=outcome.engine_id,
        model=outcome.model,
        input_tokens=None,
        output_tokens=None,
        input_candidates=candidates,
        ranked_results=outcome.ranked,
        duration_seconds=duration_seconds,
        query_type_hint=query_type_hint,
        query_entities=query_entities,
        searxng_time_range=searxng_time_range,
        payload_json=payload_json,
        run_key=run_key,
        main_span=main_span,
        logger=logger,
        preserve_raw_scores=outcome.engine_id == "voyage",
    )


async def run_llm_stage(
    *,
    query: str,
    candidates: list[WebSearchResult],
    top_k: int,
    candidate_limit: int,
    query_type_hint: str | None,
    research_goal: str | None,
    instruction: str | None,
    query_entities: list | None,
    searxng_time_range: str | None,
    run_key: str | None,
    main_span,
    logger: logging.Logger,
    session_id: str | None = None,
    ab_entity_boost: float | None = None,
    entity_boost: float = 0.15,
) -> RankedStageOutcome:
    stage_start = time.time()
    try:
        outcome = await rerank_with_llm(
            query,
            candidates,
            top_k=top_k,
            candidate_limit=candidate_limit,
            query_type_hint=query_type_hint,
            research_goal=research_goal,
            instruction=instruction,
            timeout_seconds=settings.rerank_llm_timeout_seconds,
            session_id=session_id,
        )
    except Exception as exc:
        duration_seconds = time.time() - stage_start
        logger.warning("LLM rerank failed: %s: %s", type(exc).__name__, exc)
        return RankedStageOutcome(
            candidates=candidates,
            provider="gpt-oss-worker",
            model=None,
            stage_name="llm_rerank",
            input_count=len(candidates),
            output_count=len(candidates),
            duration_seconds=duration_seconds,
            relevance_scores=[],
            max_score=0.0,
            input_tokens=None,
            output_tokens=None,
            error=exc,
        )
    duration_seconds = time.time() - stage_start
    if not outcome.ranked:
        return RankedStageOutcome(
            candidates=candidates,
            provider=outcome.endpoint_name,
            model=outcome.model,
            stage_name="llm_rerank",
            input_count=len(candidates),
            output_count=len(candidates),
            duration_seconds=duration_seconds,
            relevance_scores=[],
            max_score=0.0,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
        )
    payload_json = {
        "original_count": len(candidates),
        "llm_candidate_limit": candidate_limit,
        "entity_boost": ab_entity_boost,
    }
    return _apply_ranked_stage(
        stage_name="llm_rerank",
        provider=outcome.endpoint_name,
        model=outcome.model,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        input_candidates=candidates,
        ranked_results=outcome.ranked,
        duration_seconds=duration_seconds,
        query_type_hint=query_type_hint,
        query_entities=query_entities,
        searxng_time_range=searxng_time_range,
        payload_json=payload_json,
        run_key=run_key,
        main_span=main_span,
        logger=logger,
    )
