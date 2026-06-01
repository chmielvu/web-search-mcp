from __future__ import annotations

import logging
from collections import Counter

from ..models import WebSearchResult
from ..utils.observability import emit_observability_event, serialize_search_results


def _host_counts(results: list[WebSearchResult]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in results:
        counts[result.domain or "__unknown_domain__"] += 1
    return dict(counts.most_common(10))


def emit_merge_summary(
    logger: logging.Logger,
    *,
    result_lists: list[list[WebSearchResult]],
    output: list[WebSearchResult],
    provider_contributions: Counter[str],
    list_weights: list[float] | None,
    k: int,
    discarded_count: int,
    overlap_rate: float,
    duration_seconds: float,
    max_per_host: int,
    host_cap_top_k: int | None,
) -> None:
    emit_observability_event(
        logger,
        "search.merge.summary",
        input_list_count=len(result_lists),
        input_result_count=sum(len(results) for results in result_lists),
        output_result_count=len(output),
        input_results=result_lists,
        output_results=output,
        discarded_count=discarded_count,
        overlap_rate=round(overlap_rate, 4),
        duration_ms=round(duration_seconds * 1000, 3),
        rrf_k=k,
        list_weights=list_weights,
        provider_contributions=dict(provider_contributions),
        max_per_host=max_per_host,
        host_cap_top_k=host_cap_top_k,
        output_host_counts=_host_counts(output),
        top_results=serialize_search_results(output, max_results=5),
    )
