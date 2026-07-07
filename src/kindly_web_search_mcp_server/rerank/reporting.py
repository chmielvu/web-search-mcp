"""Rerank stage telemetry and analytics helpers."""

from __future__ import annotations

import logging
from typing import Any

from ..analytics.duckdb_store import insert_rerank_stages as analytics_insert_rerank_stages
from ..telemetry import RERANK_INPUT_COUNT, RERANK_OUTPUT_COUNT, RERANK_STAGE, record_rerank_stage


def record_bi_encoder_stage(
    *,
    original_count: int,
    output_count: int,
    duration_seconds: float,
    run_key: str | None,
    query_type_hint: str | None,
    entity_overlap_enabled: bool,
    main_span: Any,
    logger: logging.Logger,
) -> None:
    record_rerank_stage(
        stage="bi_encoder",
        input_count=original_count,
        output_count=output_count,
        duration_seconds=duration_seconds,
    )
    if run_key:
        try:
            analytics_insert_rerank_stages(
                run_key=run_key,
                stage="bi_encoder",
                provider=None,
                model=None,
                input_count=original_count,
                output_count=output_count,
                duration_ms=round(duration_seconds * 1000.0, 3),
                max_score=None,
                avg_score=None,
                query_type_hint=query_type_hint,
                entity_overlap_enabled=entity_overlap_enabled,
                payload_json={"original_count": original_count},
            )
        except Exception as exc:
            logger.debug("analytics insert_rerank_stages (bi_encoder) failed: %s", exc)
    main_span.add_event(
        "rerank.bi_encoder",
        attributes={
            RERANK_STAGE: "bi_encoder",
            RERANK_INPUT_COUNT: original_count,
            RERANK_OUTPUT_COUNT: output_count,
        },
    )


def record_ranked_stage(
    *,
    stage_name: str,
    provider: str,
    model: str | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    input_count: int,
    output_count: int,
    duration_seconds: float,
    relevance_scores: list[float],
    payload_json: dict[str, Any],
    query_type_hint: str | None,
    entity_overlap_enabled: bool,
    run_key: str | None,
    main_span: Any,
    logger: logging.Logger,
) -> tuple[float, float]:
    max_score = max(relevance_scores) if relevance_scores else 0.0
    avg_score = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0.0
    record_rerank_stage(
        stage=stage_name,
        input_count=input_count,
        output_count=output_count,
        duration_seconds=duration_seconds,
        relevance_scores=relevance_scores,
        model=model,
    )
    if run_key:
        try:
            analytics_insert_rerank_stages(
                run_key=run_key,
                stage=stage_name,
                provider=provider,
                model=model,
                model_used=model,
                input_count=input_count,
                output_count=output_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=round(duration_seconds * 1000.0, 3),
                max_score=max_score,
                avg_score=avg_score,
                query_type_hint=query_type_hint,
                entity_overlap_enabled=entity_overlap_enabled,
                payload_json=payload_json,
            )
        except Exception as exc:
            logger.debug("analytics insert_rerank_stages (%s) failed: %s", stage_name, exc)
    event_attributes = {
            RERANK_STAGE: stage_name,
            RERANK_INPUT_COUNT: input_count,
            RERANK_OUTPUT_COUNT: output_count,
            "rerank.top_score": round(max_score, 4),
            "rerank.avg_score": round(avg_score, 4),
    }
    if model is not None:
        event_attributes["rerank.model"] = model
        event_attributes["rerank.model_used"] = model
    if input_tokens is not None:
        event_attributes["rerank.input_tokens"] = input_tokens
    if output_tokens is not None:
        event_attributes["rerank.output_tokens"] = output_tokens
    main_span.add_event(
        f"rerank.{stage_name}",
        attributes=event_attributes,
    )
    return max_score, avg_score


def record_diversity_stage(
    *,
    input_count: int,
    output_count: int,
    duration_seconds: float,
    removed_count: int,
    mmr_lambda: float,
    run_key: str | None,
    query_type_hint: str | None,
    entity_overlap_enabled: bool,
    main_span: Any,
    logger: logging.Logger,
) -> None:
    record_rerank_stage(
        stage="diversity",
        input_count=input_count,
        output_count=output_count,
        duration_seconds=duration_seconds,
    )
    if run_key:
        try:
            analytics_insert_rerank_stages(
                run_key=run_key,
                stage="diversity",
                provider=None,
                model=None,
                input_count=input_count,
                output_count=output_count,
                duration_ms=round(duration_seconds * 1000.0, 3),
                max_score=None,
                avg_score=None,
                query_type_hint=query_type_hint,
                entity_overlap_enabled=entity_overlap_enabled,
                payload_json={
                    "diversity_removed": removed_count,
                    "mmr_lambda": mmr_lambda,
                },
            )
        except Exception as exc:
            logger.debug("analytics insert_rerank_stages (diversity) failed: %s", exc)
    main_span.add_event(
        "rerank.diversity",
        attributes={
            RERANK_STAGE: "diversity",
            RERANK_INPUT_COUNT: input_count,
            RERANK_OUTPUT_COUNT: output_count,
            "rerank.removed_count": removed_count,
        },
    )
