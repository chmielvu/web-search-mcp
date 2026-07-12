from __future__ import annotations

import logging
from typing import Any

from ..analytics.rerank_candidate_writes import insert_rerank_candidate_rows_batch
from ..analytics.async_writes import dispatch_duckdb_write
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
    bm25_scores: dict[str, float] | None = None,
    bm25_ranks: dict[str, int] | None = None,
    dense_scores: dict[str, float] | None = None,
    dense_ranks: dict[str, int] | None = None,
    cross_encoder_scores: dict[str, float] | None = None,
    llm_scores: dict[str, float] | None = None,
    fused_scores: dict[str, float] | None = None,
    hybrid_rrf_scores: dict[str, float] | None = None,
    recency_boosts: dict[str, float] | None = None,
    entity_overlap_scores: dict[str, float] | None = None,
) -> None:
    if not run_key:
        return
    try:
        rows = build_rerank_candidate_rows(
            run_key=run_key,
            stage=stage,
            before_candidates=before_candidates,
            after_candidates=after_candidates,
            payload_json=payload_json,
            bm25_scores=bm25_scores,
            bm25_ranks=bm25_ranks,
            dense_scores=dense_scores,
            dense_ranks=dense_ranks,
            cross_encoder_scores=cross_encoder_scores,
            llm_scores=llm_scores,
            fused_scores=fused_scores,
            hybrid_rrf_scores=hybrid_rrf_scores,
            recency_boosts=recency_boosts,
            entity_overlap_scores=entity_overlap_scores,
        )
        insert_rerank_candidate_rows_batch(rows)
    except Exception as exc:
        logger.debug("analytics insert_rerank_candidates failed: %s", exc)


def build_rerank_candidate_rows(
    *,
    run_key: str,
    stage: str,
    before_candidates: list[WebSearchResult],
    after_candidates: list[WebSearchResult],
    payload_json: dict[str, Any] | None = None,
    bm25_scores: dict[str, float] | None = None,
    bm25_ranks: dict[str, int] | None = None,
    dense_scores: dict[str, float] | None = None,
    dense_ranks: dict[str, int] | None = None,
    cross_encoder_scores: dict[str, float] | None = None,
    llm_scores: dict[str, float] | None = None,
    fused_scores: dict[str, float] | None = None,
    hybrid_rrf_scores: dict[str, float] | None = None,
    recency_boosts: dict[str, float] | None = None,
    entity_overlap_scores: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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
            row = {
                "run_key": run_key,
                "stage": stage,
                "link": link,
                "candidate_id": _candidate_id(link, candidate.title, candidate.snippet),
                "canonical_result_id": _canonical_result_id(link),
                "rank_before": before_rank,
                "rank_after": after_rank,
                "score_before": getattr(before_candidate, "score", None),
                "score_after": getattr(after_candidate, "score", None),
                "bm25_score": bm25_scores.get(link) if bm25_scores else None,
                "bm25_rank": bm25_ranks.get(link) if bm25_ranks else None,
                "dense_score": dense_scores.get(link) if dense_scores else None,
                "dense_rank": dense_ranks.get(link) if dense_ranks else None,
                "cross_encoder_raw": cross_encoder_scores.get(link) if cross_encoder_scores else None,
                "llm_raw_score": llm_scores.get(link) if llm_scores else None,
                "fused_score": fused_scores.get(link) if fused_scores else None,
                "hybrid_rrf_score": hybrid_rrf_scores.get(link) if hybrid_rrf_scores else None,
                "recency_boost": recency_boosts.get(link) if recency_boosts else None,
                "entity_overlap_score": entity_overlap_scores.get(link) if entity_overlap_scores else None,
                "diversity_removed": after_candidate is None,
                "payload_json": {
                    **(payload_json or {}),
                },
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
    dense_scores: dict[str, float] | None = None,
    dense_ranks: dict[str, int] | None = None,
    cross_encoder_scores: dict[str, float] | None = None,
    llm_scores: dict[str, float] | None = None,
    fused_scores: dict[str, float] | None = None,
    hybrid_rrf_scores: dict[str, float] | None = None,
    recency_boosts: dict[str, float] | None = None,
    entity_overlap_scores: dict[str, float] | None = None,
) -> None:
    """Queue one stage's candidate analytics without blocking rerank latency."""
    if not run_key:
        return

    def _write():
        try:
            rows = build_rerank_candidate_rows(
                run_key=run_key,
                stage=stage,
                before_candidates=before_candidates,
                after_candidates=after_candidates,
                payload_json=payload_json,
                bm25_scores=bm25_scores,
                bm25_ranks=bm25_ranks,
                dense_scores=dense_scores,
                dense_ranks=dense_ranks,
                cross_encoder_scores=cross_encoder_scores,
                llm_scores=llm_scores,
                fused_scores=fused_scores,
                hybrid_rrf_scores=hybrid_rrf_scores,
                recency_boosts=recency_boosts,
                entity_overlap_scores=entity_overlap_scores,
            )
            insert_rerank_candidate_rows_batch(rows)
        except Exception as exc:
            logger.debug("analytics insert_rerank_candidates failed: %s", exc)

    dispatch_duckdb_write(f"analytics.rerank_candidates.{stage}", _write)
