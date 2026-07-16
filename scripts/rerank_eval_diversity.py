"""Deterministic MMR grid replay against frozen post-RankLLM windows."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import itertools
import math
import statistics
from typing import Any

from kindly_web_search_mcp_server.analytics.search_relevance_judge import SearchRelevanceJudge
from kindly_web_search_mcp_server.models import WebSearchResult

from kindly_web_search_mcp_server.rerank.diversity import select_diverse_slate
from rerank_eval_common import intent_stratified_subset, ndcg_from_reference, normalized_host

LAMBDAS = (0.7, 0.8, 0.9)
TRIGGER_THRESHOLDS = (0.80, 0.85, 0.90)
HOST_CAPS = (2, 3)
AUDIT_DUPLICATE_THRESHOLD = 0.85


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(
        0.0,
        min(1.0, sum(a * b for a, b in zip(left, right, strict=True)) / left_norm / right_norm),
    )


def _duplicate_pair_rate(indices: Sequence[int], embeddings: Sequence[Sequence[float]]) -> float:
    pairs = list(itertools.combinations(indices, 2))
    if not pairs:
        return 0.0
    duplicates = sum(
        _cosine(embeddings[left], embeddings[right]) >= AUDIT_DUPLICATE_THRESHOLD
        for left, right in pairs
    )
    return duplicates / len(pairs)


def _query_metrics(
    record: dict[str, Any],
    *,
    lambda_param: float,
    similarity_threshold: float,
    max_per_host: int,
) -> dict[str, Any]:
    window = record["window"]
    embeddings = [item["embedding"] for item in window]
    urls = [item["url"] for item in window]
    selection = select_diverse_slate(
        embeddings,
        urls,
        output_size=min(15, len(window)),
        lambda_param=lambda_param,
        similarity_threshold=similarity_threshold,
        max_per_host=max_per_host,
    )
    selected = list(selection.selected_indices)
    displacement = statistics.fmean(
        abs(position - index) for position, index in enumerate(selected)
    )
    return {
        "selected_indices": selected,
        "triggered": selection.triggered,
        "max_pairwise_similarity": selection.max_pairwise_similarity,
        "host_overflow_count": selection.host_overflow_count,
        "ndcg_at15": ndcg_from_reference(selected),
        "fixed_duplicate_pair_rate": _duplicate_pair_rate(selected, embeddings),
        "unique_hosts_at5": len({normalized_host(urls[index]) for index in selected[:5]}),
        "unique_hosts_at15": len({normalized_host(urls[index]) for index in selected[:15]}),
        "mean_absolute_displacement": displacement,
    }


def _aggregate(per_query: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "query_count": len(per_query),
        "trigger_rate": statistics.fmean(float(item["triggered"]) for item in per_query),
        "mean_ndcg_at15": statistics.fmean(item["ndcg_at15"] for item in per_query),
        "mean_fixed_duplicate_pair_rate": statistics.fmean(
            item["fixed_duplicate_pair_rate"] for item in per_query
        ),
        "mean_unique_hosts_at5": statistics.fmean(item["unique_hosts_at5"] for item in per_query),
        "mean_unique_hosts_at15": statistics.fmean(item["unique_hosts_at15"] for item in per_query),
        "mean_absolute_displacement": statistics.fmean(
            item["mean_absolute_displacement"] for item in per_query
        ),
    }


def _variant_key(lambda_param: float, threshold: float, host_cap: int) -> str:
    return f"lambda={lambda_param:.1f},threshold={threshold:.2f},host_cap={host_cap}"


def tune_diversity(
    records: Sequence[dict[str, Any]],
    *,
    judge_comparison: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    if not records:
        raise ValueError("diversity tuning requires at least one frozen window")
    baseline_per_query = [
        _query_metrics(
            record,
            lambda_param=1.0,
            similarity_threshold=1.0,
            max_per_host=max(30, len(record["window"])),
        )
        for record in records
    ]
    baseline = _aggregate(baseline_per_query)
    variants: dict[str, Any] = {}
    eligible: list[tuple[float, float, int]] = []
    for lambda_param, threshold, host_cap in itertools.product(
        LAMBDAS, TRIGGER_THRESHOLDS, HOST_CAPS
    ):
        per_query = [
            _query_metrics(
                record,
                lambda_param=lambda_param,
                similarity_threshold=threshold,
                max_per_host=host_cap,
            )
            for record in records
        ]
        aggregate = _aggregate(per_query)
        key = _variant_key(lambda_param, threshold, host_cap)
        eligible_by_metrics = aggregate["mean_ndcg_at15"] >= baseline["mean_ndcg_at15"] * 0.99 and (
            aggregate["mean_fixed_duplicate_pair_rate"] < baseline["mean_fixed_duplicate_pair_rate"]
            or aggregate["mean_unique_hosts_at5"] > baseline["mean_unique_hosts_at5"]
        )
        aggregate.update(
            {
                "lambda_param": lambda_param,
                "similarity_threshold": threshold,
                "max_per_host": host_cap,
                "eligible": eligible_by_metrics,
            }
        )
        variants[key] = aggregate
        if eligible_by_metrics:
            eligible.append((lambda_param, threshold, host_cap))

    winner = None
    if eligible:
        winner = min(
            eligible,
            key=lambda item: (
                variants[_variant_key(*item)]["mean_fixed_duplicate_pair_rate"],
                -variants[_variant_key(*item)]["mean_unique_hosts_at5"],
                -variants[_variant_key(*item)]["mean_ndcg_at15"],
                -item[0],
                -item[1],
                -item[2],
            ),
        )

    rejected_by_judge = False
    if winner is not None and judge_comparison:
        unchanged = judge_comparison.get("unchanged", {})
        winning = judge_comparison.get("winner", {})
        rejected_by_judge = any(
            winning.get(dimension, 0.0) < unchanged.get(dimension, 0.0) - 0.02
            for dimension in ("relevance", "source_quality")
        )
    decision = {
        "enabled": winner is not None and not rejected_by_judge,
        "winner": (
            {
                "lambda_param": winner[0],
                "similarity_threshold": winner[1],
                "max_per_host": winner[2],
            }
            if winner is not None and not rejected_by_judge
            else None
        ),
        "rejected_by_judge": rejected_by_judge,
        "eligible_variants": [_variant_key(*item) for item in eligible],
    }
    return {
        "schema_version": 1,
        "audit_duplicate_threshold": AUDIT_DUPLICATE_THRESHOLD,
        "baseline": baseline,
        "variants": variants,
        "judge_comparison": judge_comparison,
        "decision": decision,
    }


async def judge_diversity_winner(
    records: Sequence[dict[str, Any]],
    *,
    winner: dict[str, Any],
    subset_size: int,
) -> dict[str, dict[str, float]]:
    selected_records = intent_stratified_subset(records, subset_size)
    judge = SearchRelevanceJudge()
    semaphore = asyncio.Semaphore(2)

    async def evaluate(record: dict[str, Any], variant: str) -> tuple[str, float, float]:
        if variant == "unchanged":
            indices = list(range(min(15, len(record["window"]))))
        else:
            metrics = _query_metrics(
                record,
                lambda_param=winner["lambda_param"],
                similarity_threshold=winner["similarity_threshold"],
                max_per_host=winner["max_per_host"],
            )
            indices = metrics["selected_indices"]
        results = [
            WebSearchResult(
                title=record["window"][index]["title"],
                link=record["window"][index]["url"],
                snippet=record["window"][index]["snippet"],
                domain=record["window"][index].get("domain", ""),
                providers=record["window"][index].get("providers", []),
                provider_count=record["window"][index].get("provider_count", 1),
            )
            for index in indices
        ]
        async with semaphore:
            outcome = await judge.evaluate(
                query=record["query"],
                intent=record["intent"],
                results=results,
                research_goal=record["research_goal"],
            )
        if outcome.error:
            raise RuntimeError(
                f"judge failed for {record['id']} diversity={variant}: {outcome.error}"
            )
        return variant, outcome.relevance_score, outcome.source_quality_score

    outcomes = await asyncio.gather(
        *(
            evaluate(record, variant)
            for record in selected_records
            for variant in ("unchanged", "winner")
        )
    )
    grouped = {
        variant: {
            "relevance": statistics.fmean(
                relevance for name, relevance, _ in outcomes if name == variant
            ),
            "source_quality": statistics.fmean(
                quality for name, _, quality in outcomes if name == variant
            ),
        }
        for variant in ("unchanged", "winner")
    }
    return grouped
