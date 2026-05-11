from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from collections import Counter
from typing import Any

from ..models import WebSearchResult
from ..settings import settings
from ..telemetry import (
    record_rrf_merge,
    record_rrf_score,
    record_merge,
    RRF_INPUT_LISTS,
    RRF_INPUT_TOTAL,
)
from .normalize import canonicalize_url
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


@dataclass
class _MergedCandidate:
    result: WebSearchResult
    score: float = 0.0
    providers: set[str] = field(default_factory=set)


def _result_signal(result: WebSearchResult) -> int:
    return len((result.title or "").strip()) + len((result.snippet or "").strip())


def _pick_better(base: WebSearchResult, candidate: WebSearchResult) -> WebSearchResult:
    if _result_signal(candidate) > _result_signal(base):
        return candidate
    return base


def merge_search_results(
    result_lists: list[list[WebSearchResult]],
    *,
    k: int | None = None,
    provider_weights: dict[str, float] | None = None,
    enable_telemetry: bool = True,
) -> list[WebSearchResult]:
    """Merge multiple ranked lists using Weighted Reciprocal Rank Fusion.

    Formula: score += w_provider × 1/(k + rank)

    Args:
        result_lists: Lists of results from different providers/queries.
        k: RRF constant. Lower = more rank-sensitive. Default from settings.
        provider_weights: Per-provider weight multipliers. Default from settings.
        enable_telemetry: If True, record RRF metrics (default True).
    """
    tracer = trace.get_tracer("web-search-mcp") if enable_telemetry else None

    # Count total input results
    total_input = sum(len(results) for results in result_lists)

    # Track overlap: URLs appearing in multiple lists
    url_occurrences: Counter = Counter()
    for results in result_lists:
        for result in results:
            key = canonicalize_url(result.link)
            url_occurrences[key] += 1

    overlapping_urls = [url for url, count in url_occurrences.items() if count > 1]
    overlap_rate = len(overlapping_urls) / len(url_occurrences) if url_occurrences else 0.0

    if k is None:
        k = settings.rrf_k
    weights = provider_weights or settings.rrf_provider_weights

    start_time = time.time()

    merged: dict[str, _MergedCandidate] = {}
    encounter_order: dict[str, int] = {}
    order_counter = 0

    for results in result_lists:
        for rank, result in enumerate(results, start=1):
            key = canonicalize_url(result.link)
            if key not in merged:
                merged[key] = _MergedCandidate(result=result)
                encounter_order[key] = order_counter
                order_counter += 1

            # Weighted RRF: use max weight among result's provider tags
            result_providers = result.providers or []
            w = max((weights.get(p, 1.0) for p in result_providers), default=1.0)
            merged[key].score += w * (1.0 / (k + rank))

            if result_providers:
                merged[key].providers.update(p for p in result_providers if p)

            merged[key].result = _pick_better(merged[key].result, result)

    ranked = sorted(
        merged.items(),
        key=lambda item: (-item[1].score, encounter_order[item[0]]),
    )

    output: list[WebSearchResult] = []
    for _, bucket in ranked:
        result = bucket.result.model_copy(
            update={
                "providers": sorted(bucket.providers) or bucket.result.providers,
                "score": bucket.score,
            }
        )
        output.append(result)

    # Calculate discarded count and provider contribution
    discarded_count = total_input - len(output)

    # Count provider contribution in final output
    provider_contributions: Counter = Counter()
    for result in output:
        for provider in (result.providers or []):
            provider_contributions[provider] += 1

    duration_seconds = time.time() - start_time

    # Record telemetry metrics
    if enable_telemetry:
        # Record basic merge metrics
        record_merge(duration_seconds, len(result_lists), len(output))

        # Record detailed RRF metrics
        record_rrf_merge(
            input_lists=len(result_lists),
            input_total=total_input,
            output_total=len(output),
            discarded_count=discarded_count,
            overlap_rate=overlap_rate,
            provider_contributions=dict(provider_contributions),
        )

        # Record individual RRF scores for distribution analysis
        for i, result in enumerate(output[:10]):
            if result.score is not None:
                record_rrf_score(result.score, i + 1)

    # Add span events with RRF details
    with tracer.start_as_current_span(
        "rrf_merge",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            RRF_INPUT_LISTS: len(result_lists),
            RRF_INPUT_TOTAL: total_input,
            "merge.k": k,
            "merge.output_total": len(output),
            "merge.discarded_count": discarded_count,
            "merge.overlap_rate": round(overlap_rate, 3),
        },
    ) as span:
        # Add provider contribution events
        for provider, count in provider_contributions.items():
            span.add_event(f"rrf.provider.{provider}", attributes={
                "provider.name": provider,
                "rrf.provider_contribution": count,
            })

        # Add discarded sample
        if discarded_count > 0:
            span.add_event("rrf.discards", attributes={
                "rrf.discarded_count": discarded_count,
            })

        # Add overlap sample
        if overlapping_urls:
            span.add_event("rrf.overlap", attributes={
                "rrf.overlapping_count": len(overlapping_urls),
            })

    logger.debug(
        "RRF merge: k=%d, %d lists → %d unique results (discarded=%d, overlap=%.2f). Top 5: %s",
        k, len(result_lists), len(output), discarded_count, overlap_rate,
        [(r.link[:50], f"{r.score:.5f}") for r in output[:5]],
    )

    return output

