from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from collections import Counter
from urllib.parse import urlparse
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


def _pick_better(
    base: WebSearchResult,
    candidate: WebSearchResult,
    weights: dict[str, float],
) -> WebSearchResult:
    base_w = max((weights.get(p, 1.0) for p in base.providers or []), default=1.0)
    cand_w = max((weights.get(p, 1.0) for p in candidate.providers or []), default=1.0)
    if cand_w > base_w:
        return candidate
    if cand_w == base_w and len(candidate.snippet or "") > len(base.snippet or ""):
        return candidate
    return base


def _normalize_host(link: str, fallback_domain: str | None = None) -> str:
    host = (urlparse(link).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host:
        return host
    return (fallback_domain or "").strip().lower() or "__unknown_host__"


def _apply_host_cap(
    ranked: list[tuple[str, _MergedCandidate]],
    encounter_order: dict[str, int],
    *,
    max_per_host: int,
    top_k: int,
) -> list[tuple[str, _MergedCandidate]]:
    """Reduce host clustering while preserving deterministic ranking semantics."""
    if max_per_host <= 0 or top_k <= 0:
        return ranked

    capped_count: Counter[str] = Counter()
    selected: list[tuple[str, _MergedCandidate]] = []
    overflow: list[tuple[str, _MergedCandidate]] = []

    # First pass: preserve each host's best results up to cap.
    for key, candidate in ranked:
        host = _normalize_host(candidate.result.link, candidate.result.domain)
        if len(selected) < top_k and capped_count[host] < max_per_host:
            selected.append((key, candidate))
            capped_count[host] += 1
        else:
            overflow.append((key, candidate))

    if len(selected) >= top_k:
        return selected + overflow

    # Second pass: interleave remaining hosts (deterministic via encounter order).
    overflow_by_host: dict[str, list[tuple[str, _MergedCandidate]]] = {}
    host_order: dict[str, int] = {}
    for key, candidate in overflow:
        host = _normalize_host(candidate.result.link, candidate.result.domain)
        overflow_by_host.setdefault(host, []).append((key, candidate))
        host_order.setdefault(host, encounter_order[key])

    for host in overflow_by_host:
        overflow_by_host[host].sort(key=lambda x: -x[1].score)

    host_cycle = sorted(host_order, key=lambda host: host_order[host])
    while len(selected) < top_k and host_cycle:
        next_cycle: list[str] = []
        for host in host_cycle:
            queue = overflow_by_host.get(host)
            if not queue:
                continue
            selected.append(queue.pop(0))
            if len(selected) >= top_k:
                break
            if queue:
                next_cycle.append(host)
        host_cycle = next_cycle

    remaining = [item for host in host_order for item in overflow_by_host.get(host, [])]
    return selected + remaining


def merge_search_results(
    result_lists: list[list[WebSearchResult]],
    *,
    k: int | None = None,
    provider_weights: dict[str, float] | None = None,
    max_per_host: int = 2,
    host_cap_top_k: int | None = None,
    enable_telemetry: bool = True,
) -> list[WebSearchResult]:
    """Merge multiple ranked lists using Weighted Reciprocal Rank Fusion.

    Formula: score += w_provider × 1/(k + rank)

    Args:
        result_lists: Lists of results from different providers/queries.
        k: RRF constant. Lower = more rank-sensitive. Default from settings.
        provider_weights: Per-provider weight multipliers. Default from settings.
        max_per_host: Maximum results per host in the top-k diversification window.
        host_cap_top_k: Size of the diversification window. Defaults to all ranked results.
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

            merged[key].result = _pick_better(merged[key].result, result, weights)

    ranked = sorted(
        merged.items(),
        key=lambda item: (-item[1].score, encounter_order[item[0]]),
    )

    capped_ranked = _apply_host_cap(
        ranked,
        encounter_order,
        max_per_host=max_per_host,
        top_k=host_cap_top_k or len(ranked),
    )

    output: list[WebSearchResult] = []
    for _, bucket in capped_ranked:
        result = bucket.result.model_copy(
            update={
                "providers": sorted(bucket.providers) or bucket.result.providers,
                "provider_count": len(bucket.providers),
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
    if tracer is not None:
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
