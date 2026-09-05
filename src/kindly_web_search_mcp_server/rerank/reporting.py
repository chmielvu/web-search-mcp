"""Rerank stage telemetry helpers."""

from __future__ import annotations

from typing import Any

from ..telemetry import RERANK_INPUT_COUNT, RERANK_OUTPUT_COUNT, RERANK_STAGE, record_rerank_stage


def record_bi_encoder_stage(
    *,
    original_count: int,
    output_count: int,
    duration_seconds: float,
    main_span: Any,
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
    attempted_passes: int = 0,
    valid_passes: int = 0,
    failed_passes: int = 0,
    main_span: object,
) -> tuple[float | None, float | None]:
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
    event_attributes: dict[str, object] = {
        RERANK_STAGE: stage_name,
        RERANK_INPUT_COUNT: input_count,
        RERANK_OUTPUT_COUNT: output_count,
        "rerank.top_score": round(max_score, 4),
        "rerank.avg_score": round(avg_score, 4),
    }
    if provider:
        event_attributes["rerank.provider"] = provider
    if model is not None:
        event_attributes["rerank.model"] = model
    if input_tokens is not None:
        event_attributes["rerank.input_tokens"] = input_tokens
    if output_tokens is not None:
        event_attributes["rerank.output_tokens"] = output_tokens
    if attempted_passes:
        event_attributes["rerank.attempted_passes"] = attempted_passes
        event_attributes["rerank.valid_passes"] = valid_passes
        event_attributes["rerank.failed_passes"] = failed_passes
    main_span.add_event(
        f"rerank.{stage_name}",
        attributes=event_attributes,
    )
    return max_score, avg_score
