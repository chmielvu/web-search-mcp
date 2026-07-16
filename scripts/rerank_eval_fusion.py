"""Frozen-input replay and deterministic selection for the global RRF constant."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import statistics
from typing import Any

from kindly_web_search_mcp_server.analytics.search_relevance_judge import SearchRelevanceJudge
from kindly_web_search_mcp_server.models import WebSearchResult

from rerank_eval_common import hybrid_rrf_order, intent_stratified_subset, jaccard_at, rbo

RRF_VARIANTS = (20, 40, 60, 80)
JUDGE_DIMENSIONS = ("overall", "research_goal_usefulness", "source_quality", "relevance")


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _coverage(order: list[str], documents: dict[str, Any], field: str) -> int:
    values: set[str] = set()
    for item_id in order[:15]:
        document = documents[item_id]
        if field == "providers":
            values.update(str(value) for value in document.get("providers", []))
        else:
            value = str(document.get(field, "")).strip().lower()
            if value:
                values.add(value)
    return len(values)


def _as_result(document: dict[str, Any]) -> WebSearchResult:
    return WebSearchResult(
        title=document["title"],
        link=document["link"],
        snippet=document["snippet"],
        domain=document.get("domain", ""),
        providers=document.get("providers", []),
        provider_count=document.get("provider_count", 1),
    )


async def attach_rrf_judgments(
    records: Sequence[dict[str, Any]],
    *,
    subset_size: int,
) -> list[dict[str, Any]]:
    selected = intent_stratified_subset(records, subset_size)
    judge = SearchRelevanceJudge()
    semaphore = asyncio.Semaphore(2)

    async def judge_variant(record: dict[str, Any], k: int) -> tuple[str, int, dict[str, float]]:
        order = hybrid_rrf_order(record, k)[:15]
        results = [_as_result(record["documents"][item_id]) for item_id in order]
        async with semaphore:
            outcome = await judge.evaluate(
                query=record["query"],
                intent=record["intent"],
                results=results,
                research_goal=record["research_goal"],
            )
        if outcome.error:
            raise RuntimeError(f"judge failed for {record['id']} k={k}: {outcome.error}")
        return (
            record["id"],
            k,
            {
                "overall": outcome.overall_score,
                "research_goal_usefulness": outcome.completeness_score,
                "source_quality": outcome.source_quality_score,
                "relevance": outcome.relevance_score,
            },
        )

    outcomes = await asyncio.gather(
        *(judge_variant(record, k) for record in selected for k in RRF_VARIANTS)
    )
    scores_by_id: dict[str, dict[str, dict[str, float]]] = {}
    for record_id, k, scores in outcomes:
        scores_by_id.setdefault(record_id, {})[str(k)] = scores
    enriched = [dict(record) for record in records]
    for record in enriched:
        if record["id"] in scores_by_id:
            record["judge_scores"] = scores_by_id[record["id"]]
    return enriched


def _judge_means(records: Sequence[dict[str, Any]], k: int) -> dict[str, float | None]:
    dimensions: dict[str, list[float]] = {name: [] for name in JUDGE_DIMENSIONS}
    for record in records:
        scores = record.get("judge_scores", {}).get(str(k), {})
        for name in JUDGE_DIMENSIONS:
            value = scores.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                dimensions[name].append(float(value))
    return {name: _mean(values) for name, values in dimensions.items()}


def tune_rrf(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ValueError("RRF tuning requires at least one frozen query")
    baseline_orders = [hybrid_rrf_order(record, 60) for record in records]
    variants: dict[str, Any] = {}
    for k in RRF_VARIANTS:
        orders = [hybrid_rrf_order(record, k) for record in records]
        variants[str(k)] = {
            "k": k,
            "query_count": len(records),
            "mean_top15_jaccard_vs_k60": statistics.fmean(
                jaccard_at(order, baseline, 15)
                for order, baseline in zip(orders, baseline_orders, strict=True)
            ),
            "mean_rbo_vs_k60": statistics.fmean(
                rbo(order, baseline)
                for order, baseline in zip(orders, baseline_orders, strict=True)
            ),
            "mean_provider_coverage_at15": statistics.fmean(
                _coverage(order, record["documents"], "providers")
                for order, record in zip(orders, records, strict=True)
            ),
            "mean_domain_coverage_at15": statistics.fmean(
                _coverage(order, record["documents"], "domain")
                for order, record in zip(orders, records, strict=True)
            ),
            "judge": _judge_means(records, k),
        }

    baseline = variants["60"]["judge"]
    eligible: list[int] = []
    for k in RRF_VARIANTS:
        if k == 60:
            continue
        judged = variants[str(k)]["judge"]
        if baseline["overall"] is None or judged["overall"] is None:
            continue
        if any(
            baseline[name] is not None
            and judged[name] is not None
            and judged[name] < baseline[name] - 0.02
            for name in JUDGE_DIMENSIONS
        ):
            continue
        if judged["overall"] < baseline["overall"] + 0.01:
            continue
        eligible.append(k)

    selected = 60
    if eligible:
        selected = max(
            eligible,
            key=lambda k: (
                variants[str(k)]["judge"]["overall"],
                variants[str(k)]["judge"]["research_goal_usefulness"],
                variants[str(k)]["judge"]["source_quality"],
                variants[str(k)]["judge"]["relevance"],
                -abs(k - 60),
            ),
        )
    return {
        "schema_version": 1,
        "query_count": len(records),
        "variants": variants,
        "decision": {
            "selected_k": selected,
            "baseline_k": 60,
            "eligible_alternatives": eligible,
            "reason": "qualified_judge_improvement" if eligible else "retain_k60_guard",
        },
    }
