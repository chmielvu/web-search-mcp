from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

from opentelemetry import trace

from ..models import WebSearchResult
from ..settings import settings
from ..telemetry import record_merge, record_rrf_merge, record_rrf_score
from .merge_observability import emit_merge_summary
from .normalize import canonicalize_url

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


@dataclass
class _MergedCandidate:
    result: WebSearchResult
    score: float = 0.0
    providers: set[str] = field(default_factory=set)


def _pick_better(base: WebSearchResult, candidate: WebSearchResult) -> WebSearchResult:
    return candidate if len(candidate.snippet or "") > len(base.snippet or "") else base


def reciprocal_rank_fusion(
    result_lists: Sequence[Sequence[WebSearchResult]],
    *,
    k: int = 60,
) -> list[tuple[WebSearchResult, float]]:
    """Merge ranked lists using Reciprocal Rank Fusion."""
    merged: dict[str, _MergedCandidate] = {}
    encounter_order: dict[str, int] = {}
    for results in result_lists:
        seen_in_list = set()
        for rank, result in enumerate(results, start=1):
            key = canonicalize_url(result.link)
            if key in seen_in_list:
                continue
            seen_in_list.add(key)
            if key not in merged:
                merged[key] = _MergedCandidate(result=result, providers=set(result.providers or []))
                encounter_order[key] = len(encounter_order)
            bucket = merged[key]
            bucket.score += 1.0 / (k + rank)
            bucket.providers.update(provider for provider in result.providers or [] if provider)
            bucket.result = _pick_better(bucket.result, result)

    ranked = sorted(merged.items(), key=lambda item: (-item[1].score, encounter_order[item[0]]))
    return [
        (
            bucket.result.model_copy(
                update={
                    "providers": sorted(bucket.providers) or bucket.result.providers,
                    "provider_count": len(bucket.providers),
                }
            ),
            bucket.score,
        )
        for _, bucket in ranked
    ]


def merge_search_results(
    result_lists: list[list[WebSearchResult]],
    *,
    k: int | None = None,
    enable_telemetry: bool = False,
    run_key: str | None = None,
) -> list[WebSearchResult]:
    """Merge ranked lists using pure rank-based Reciprocal Rank Fusion."""
    total_input = sum(len(results) for results in result_lists)
    url_occurrences: Counter[str] = Counter()
    for results in result_lists:
        for result in results:
            url_occurrences[canonicalize_url(result.link)] += 1
    overlapping_urls = [url for url, count in url_occurrences.items() if count > 1]
    overlap_rate = len(overlapping_urls) / len(url_occurrences) if url_occurrences else 0.0
    effective_k = settings.rrf_k if k is None else k
    start_time = time.time()

    fused = reciprocal_rank_fusion(result_lists, k=effective_k)
    output = []
    for result, score in fused:
        output.append(result.model_copy(update={"score": score}))

    discarded_count = total_input - len(output)
    provider_contributions: Counter[str] = Counter()
    for result in output:
        provider_contributions.update(result.providers or [])
    duration_seconds = time.time() - start_time
    emit_merge_summary(
        logger,
        result_lists=result_lists,
        output=output,
        provider_contributions=provider_contributions,
        k=effective_k,
        discarded_count=discarded_count,
        overlap_rate=overlap_rate,
        duration_seconds=duration_seconds,
    )
    if enable_telemetry:
        record_merge(duration_seconds, len(result_lists), len(output))
        record_rrf_merge(
            input_lists=len(result_lists),
            input_total=total_input,
            output_total=len(output),
            discarded_count=discarded_count,
            overlap_rate=overlap_rate,
            provider_contributions=dict(provider_contributions),
        )
        for rank, result in enumerate(output[:10], start=1):
            if result.score is not None:
                record_rrf_score(result.score, rank)
    return output
