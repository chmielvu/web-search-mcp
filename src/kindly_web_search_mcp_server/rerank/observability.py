from __future__ import annotations

import logging
from typing import Any

from ..models import WebSearchResult
from ..utils.observability import emit_observability_event, serialize_search_results


def emit_rerank_stage(
    logger: logging.Logger,
    *,
    stage: str,
    query: str,
    input_count: int,
    output_count: int,
    duration_seconds: float,
    status: str,
    error: BaseException | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    fields: dict[str, Any] = {
        "stage": stage,
        "query": query,
        "input_count": input_count,
        "output_count": output_count,
        "duration_ms": round(duration_seconds * 1000, 3),
        "status": status,
    }
    if error is not None:
        fields["error_type"] = type(error).__name__
        fields["error_message"] = str(error)
    if extra:
        fields.update(extra)
    emit_observability_event(logger, "search.rerank.stage", **fields)


def emit_rerank_summary(
    logger: logging.Logger,
    *,
    provider: str,
    model: str,
    query: str,
    input_count: int,
    output: list[WebSearchResult],
    top_k: int,
    duration_seconds: float,
    score_threshold: float,
    max_score: float,
    instruction_present: bool = False,
    instruction_length: int | None = None,
    query_type_hint: str | None = None,
) -> None:
    emit_observability_event(
        logger,
        "search.rerank.summary",
        provider=provider,
        model=model,
        query=query,
        input_count=input_count,
        output_count=len(output),
        top_k=top_k,
        duration_ms=round(duration_seconds * 1000, 3),
        score_threshold=round(score_threshold, 6),
        max_score=round(max_score, 6),
        instruction_present=instruction_present,
        instruction_length=instruction_length,
        query_type_hint=query_type_hint,
        results=output,
        top_results=serialize_search_results(output, max_results=min(top_k, 5)),
    )


# Policy-level rerank events (Task 3.2). Core and policy use emit_observability_event
# directly for "rerank.eligibility|engine_selected|completed|bypassed" to keep
# payload flexible; these helpers exist for future standardization and to satisfy
# the plan's "modify rerank/observability.py".
def emit_rerank_policy_decision(
    logger: logging.Logger,
    *,
    decision: str,
    **fields: Any,
) -> None:
    emit_observability_event(logger, f"rerank.{decision}", **fields)
