from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from ..models import WebSearchResult
from ..utils.observability import (
    emit_observability_event,
    preview_text,
    serialize_search_results,
)


def _domain_counts(results: list[WebSearchResult]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in results:
        counts[result.domain or "__unknown_domain__"] += 1
    return dict(counts.most_common(10))


def _provider_counts(results: list[WebSearchResult]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in results:
        providers = result.providers or ["__unknown_provider__"]
        for provider in providers:
            counts[provider] += 1
    return dict(counts.most_common(10))


def serialize_query_variants(variants: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for index, variant in enumerate(variants):
        serialized.append(
            {
                "index": index,
                "kind": getattr(variant, "kind", None),
                "branch_type": getattr(variant, "branch_type", None),
                "target": getattr(variant, "target", None),
                "query": preview_text(getattr(variant, "query", ""), limit=1000),
                "weight": getattr(variant, "weight", None),
                "why": preview_text(getattr(variant, "why", ""), limit=1000),
                "reason": preview_text(getattr(variant, "reason", ""), limit=1000),
                "must_keep_terms": getattr(variant, "must_keep_terms", []) or [],
                "max_results": getattr(variant, "max_results", None),
            }
        )
    return serialized


def summarize_result_list(
    *,
    index: int,
    query: str,
    providers: list[str] | None,
    weight: float,
    results: list[WebSearchResult],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "index": index,
        "query": preview_text(query, limit=1000),
        "providers": providers or [],
        "weight": weight,
        "result_count": len(results),
        "provider_counts": _provider_counts(results),
        "domain_counts": _domain_counts(results),
        "results": results,
        "top_results": serialize_search_results(results, max_results=3),
    }
    if metadata:
        summary.update(metadata)
    return summary


def emit_result_lists_summary(
    logger: logging.Logger,
    event: str,
    *,
    query: str,
    result_lists: list[list[WebSearchResult]],
    branch_queries: list[str],
    branch_providers: list[list[str] | None],
    branch_metadata: list[dict[str, Any]] | None = None,
) -> None:
    summaries = [
        summarize_result_list(
            index=index,
            query=branch_queries[index] if index < len(branch_queries) else query,
            providers=branch_providers[index] if index < len(branch_providers) else None,
            weight=1.0,
            results=results,
            metadata=(
                branch_metadata[index] if branch_metadata and index < len(branch_metadata) else None
            ),
        )
        for index, results in enumerate(result_lists)
    ]
    emit_observability_event(
        logger,
        event,
        query=query,
        branch_count=len(result_lists),
        total_candidate_count=sum(len(results) for results in result_lists),
        branches=summaries,
    )
