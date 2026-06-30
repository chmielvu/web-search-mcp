from __future__ import annotations

import logging
from typing import Any

from ..analytics.duckdb_store import insert_rerank_candidates as analytics_insert_rerank_candidates
from ..analytics.observability_store import _candidate_id, _canonical_result_id
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


def record_rerank_candidate_rows(
    logger: logging.Logger,
    *,
    run_key: str | None,
    stage: str,
    before_candidates: list[WebSearchResult],
    after_candidates: list[WebSearchResult],
    payload_json: dict[str, Any] | None = None,
) -> None:
    if not run_key:
        return
    try:
        before_by_link = {
            candidate.link: (index + 1, candidate)
            for index, candidate in enumerate(before_candidates)
        }
        after_by_link = {
            candidate.link: (index + 1, candidate)
            for index, candidate in enumerate(after_candidates)
        }
        all_links = list(before_by_link)
        for link in after_by_link:
            if link not in before_by_link:
                all_links.append(link)
        for link in all_links:
            before_rank, before_candidate = before_by_link.get(link, (None, None))
            after_rank, after_candidate = after_by_link.get(link, (None, None))
            candidate = after_candidate or before_candidate
            if candidate is None:
                continue
            analytics_insert_rerank_candidates(
                run_key=run_key,
                stage=stage,
                link=link,
                rank_before=before_rank,
                rank_after=after_rank,
                score_before=getattr(before_candidate, "score", None),
                score_after=getattr(after_candidate, "score", None),
                score_after_relevance=getattr(after_candidate, "score", None),
                score_after_recency=None,
                score_after_entity=None,
                recency_boost=None,
                entity_overlap_score=None,
                diversity_removed=after_candidate is None,
                payload_json={
                    **(payload_json or {}),
                    "candidate_id": _candidate_id(link, candidate.title, candidate.snippet),
                    "canonical_result_id": _canonical_result_id(link),
                },
            )
    except Exception as exc:
        logger.debug("analytics insert_rerank_candidates failed: %s", exc)
