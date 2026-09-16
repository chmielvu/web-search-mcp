"""Rerank stage analytics: candidate rows, funnel uplift, and summary events."""

from __future__ import annotations

import logging
from typing import Any

from ..analytics.producers import emit_observability_event
from ..models import WebSearchResult
from ..telemetry import RERANK_INPUT_COUNT, RERANK_OUTPUT_COUNT, RERANK_STAGE, record_rerank_stage
from ..utils.observability import serialize_search_results
from ..utils.url_canonicalize import canonicalize_url
from .async_writes import dispatch_duckdb_write
from .ids import _candidate_id, _canonical_result_id
from .rerank_candidate_writes import insert_rerank_candidate_rows_batch
from .writers import insert_funnel_uplift_batches


def emit_rerank_summary(
    logger: logging.Logger,
    *,
    provider: str | None,
    model: str | None,
    query: str,
    input_count: int,
    output: list[WebSearchResult],
    top_k: int,
    duration_seconds: float,
    max_score: float | None,
    terminal_stage: str,
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
        max_score=round(max_score, 6) if max_score is not None else None,
        terminal_stage=terminal_stage,
        top_results=serialize_search_results(output, max_results=min(top_k, 5)),
    )


def build_rerank_candidate_rows(
    *,
    run_key: str,
    stage: str,
    before_candidates: list[WebSearchResult],
    after_candidates: list[WebSearchResult],
    payload_json: dict[str, Any] | None = None,
    bm25_scores: dict[str, float] | None = None,
    bm25_ranks: dict[str, int] | None = None,
    bi_encoder_scores: dict[str, float] | None = None,
    bi_encoder_ranks: dict[str, int] | None = None,
    cross_encoder_scores: dict[str, float] | None = None,
    rankllm_scores: dict[str, float] | None = None,
    retrieval_rrf_scores: dict[str, float] | None = None,
    recency_scores: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def _lookup(
        mapping: dict[str, float] | dict[str, int] | None,
        canonical_link: str,
        source_link: str,
    ) -> float | int | None:
        if mapping is None:
            return None
        return mapping.get(canonical_link, mapping.get(source_link))

    def _lookup_or_candidate(
        mapping: dict[str, float] | dict[str, int] | None,
        canonical_link: str,
        source_link: str,
        candidate: WebSearchResult,
        attribute: str,
    ) -> float | int | None:
        value = _lookup(mapping, canonical_link, source_link)
        return value if value is not None else getattr(candidate, attribute, None)

    try:
        before_by_link: dict[str, tuple[int, WebSearchResult, str]] = {}
        for index, candidate in enumerate(before_candidates):
            canonical_link = canonicalize_url(candidate.link)
            before_by_link.setdefault(canonical_link, (index + 1, candidate, candidate.link))

        after_by_link: dict[str, tuple[int, WebSearchResult, str]] = {}
        for index, candidate in enumerate(after_candidates):
            canonical_link = canonicalize_url(candidate.link)
            after_by_link.setdefault(canonical_link, (index + 1, candidate, candidate.link))

        all_links = list(before_by_link)
        for canonical_link in after_by_link:
            if canonical_link not in before_by_link:
                all_links.append(canonical_link)

        for canonical_link in all_links:
            before_entry = before_by_link.get(canonical_link)
            after_entry = after_by_link.get(canonical_link)
            before_rank, before_candidate, before_link = before_entry or (None, None, "")
            after_rank, after_candidate, after_link = after_entry or (None, None, "")
            candidate = after_candidate or before_candidate
            if candidate is None:
                continue

            source_link = after_link or before_link or canonical_link
            row = {
                "run_key": run_key,
                "stage": stage,
                "link": canonical_link,
                "candidate_id": _candidate_id(canonical_link, candidate.title, candidate.snippet),
                "canonical_result_id": _canonical_result_id(canonical_link),
                "rank_before": before_rank,
                "rank_after": after_rank,
                "final_score_before": getattr(before_candidate, "final_score", None),
                "final_score_after": getattr(after_candidate, "final_score", None),
                "bm25_score": _lookup(bm25_scores, canonical_link, source_link),
                "bm25_rank": _lookup(bm25_ranks, canonical_link, source_link),
                "bi_encoder_score": _lookup_or_candidate(
                    bi_encoder_scores,
                    canonical_link,
                    source_link,
                    candidate,
                    "bi_encoder_score",
                ),
                "bi_encoder_rank": _lookup(bi_encoder_ranks, canonical_link, source_link),
                "cross_encoder_score": _lookup_or_candidate(
                    cross_encoder_scores,
                    canonical_link,
                    source_link,
                    candidate,
                    "cross_encoder_score",
                ),
                "rankllm_score": _lookup_or_candidate(
                    rankllm_scores,
                    canonical_link,
                    source_link,
                    candidate,
                    "rankllm_score",
                ),
                "retrieval_rrf_score": _lookup_or_candidate(
                    retrieval_rrf_scores,
                    canonical_link,
                    source_link,
                    candidate,
                    "retrieval_rrf_score",
                ),
                "recency_score": _lookup_or_candidate(
                    recency_scores,
                    canonical_link,
                    source_link,
                    candidate,
                    "recency_score",
                ),
                "diversity_penalty": getattr(candidate, "diversity_penalty", None),
                "survived": after_candidate is not None,
                "diversity_removed": stage == "mmr_fallback" and after_candidate is None,
                "payload_json": {**(payload_json or {})},
            }
            rows.append(row)
    except Exception as exc:
        raise ValueError("failed to build rerank candidate rows") from exc
    return rows


async def record_rerank_candidate_rows_async(
    logger: logging.Logger,
    *,
    run_key: str | None,
    stage: str,
    before_candidates: list[WebSearchResult],
    after_candidates: list[WebSearchResult],
    payload_json: dict[str, Any] | None = None,
    bm25_scores: dict[str, float] | None = None,
    bm25_ranks: dict[str, int] | None = None,
    bi_encoder_scores: dict[str, float] | None = None,
    bi_encoder_ranks: dict[str, int] | None = None,
    cross_encoder_scores: dict[str, float] | None = None,
    rankllm_scores: dict[str, float] | None = None,
    retrieval_rrf_scores: dict[str, float] | None = None,
    recency_scores: dict[str, float] | None = None,
) -> None:
    """Queue one stage's candidate analytics without blocking rerank latency."""
    if not run_key:
        return

    def _write() -> None:
        try:
            rows = build_rerank_candidate_rows(
                run_key=run_key,
                stage=stage,
                before_candidates=before_candidates,
                after_candidates=after_candidates,
                payload_json=payload_json,
                bm25_scores=bm25_scores,
                bm25_ranks=bm25_ranks,
                bi_encoder_scores=bi_encoder_scores,
                bi_encoder_ranks=bi_encoder_ranks,
                cross_encoder_scores=cross_encoder_scores,
                rankllm_scores=rankllm_scores,
                retrieval_rrf_scores=retrieval_rrf_scores,
                recency_scores=recency_scores,
            )
            insert_rerank_candidate_rows_batch(rows)
            stage_execution_id = _canonical_result_id(f"{run_key}|{stage}")
            stage_event_rows = [
                {
                    "stage_execution_id": stage_execution_id,
                    "run_key": run_key,
                    "canonical_result_id": row["canonical_result_id"],
                    "entered": row.get("rank_before") is None and row.get("rank_after") is not None,
                    "survived": row.get("rank_after") is not None,
                    "rank_before": row.get("rank_before"),
                    "rank_after": row.get("rank_after"),
                    "score_before": row.get("final_score_before"),
                    "score_after": row.get("final_score_after"),
                    "score_name": stage,
                    "removal_reason": (
                        None if row.get("rank_after") is not None else "rerank_stage_removed"
                    ),
                    "payload_json": row.get("payload_json"),
                }
                for row in rows
            ]
            insert_funnel_uplift_batches(candidate_stage_events=stage_event_rows)
        except Exception as exc:
            logger.warning("analytics insert_rerank_candidates failed: %s", exc)

    dispatch_duckdb_write(f"analytics.rerank_candidates.{stage}", _write)


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
