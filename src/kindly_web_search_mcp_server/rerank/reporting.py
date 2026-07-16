"""Rerank stage telemetry and analytics helpers."""

from __future__ import annotations

import logging
from typing import Any

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
    payload_json: dict[str, Any] | None = None,
) -> None:
    record_rerank_stage(
        stage="bi_encoder",
        input_count=original_count,
        output_count=output_count,
        duration_seconds=duration_seconds,
    )
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
) -> tuple[float | None, float | None]:
    if stage_name == "rankllm":
        max_score = None
        avg_score = None
    else:
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
    event_attributes = {
        RERANK_STAGE: stage_name,
        RERANK_INPUT_COUNT: input_count,
        RERANK_OUTPUT_COUNT: output_count,
    }
    if max_score is not None:
        event_attributes["rerank.top_score"] = round(max_score, 4)
    if avg_score is not None:
        event_attributes["rerank.avg_score"] = round(avg_score, 4)
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
