from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from opentelemetry import trace

from ..analytics.duckdb_store import insert_merged_candidates as analytics_insert_merged_candidates
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


def _normalize_host(link: str, fallback_domain: str | None = None) -> str:
    host = (urlparse(link).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or (fallback_domain or "").strip().lower() or "__unknown_host__"


def _apply_host_cap(
    ranked: list[tuple[str, _MergedCandidate]],
    encounter_order: dict[str, int],
    *,
    max_per_host: int,
    top_k: int,
) -> list[tuple[str, _MergedCandidate]]:
    if max_per_host <= 0 or top_k <= 0:
        return ranked
    capped: Counter[str] = Counter()
    selected: list[tuple[str, _MergedCandidate]] = []
    overflow: list[tuple[str, _MergedCandidate]] = []
    for key, candidate in ranked:
        host = _normalize_host(candidate.result.link, candidate.result.domain)
        if len(selected) < top_k and capped[host] < max_per_host:
            selected.append((key, candidate))
            capped[host] += 1
        else:
            overflow.append((key, candidate))
    if len(selected) >= top_k:
        return selected + overflow
    by_host: dict[str, list[tuple[str, _MergedCandidate]]] = {}
    host_order: dict[str, int] = {}
    for key, candidate in overflow:
        host = _normalize_host(candidate.result.link, candidate.result.domain)
        by_host.setdefault(host, []).append((key, candidate))
        host_order.setdefault(host, encounter_order[key])
    for queue in by_host.values():
        queue.sort(key=lambda item: -item[1].score)
    host_cycle = sorted(host_order, key=host_order.get)
    while len(selected) < top_k and host_cycle:
        next_cycle: list[str] = []
        for host in host_cycle:
            queue = by_host[host]
            if queue:
                selected.append(queue.pop(0))
                if queue and len(selected) < top_k:
                    next_cycle.append(host)
            if len(selected) >= top_k:
                break
        host_cycle = next_cycle
    return selected + [item for queue in by_host.values() for item in queue]


def merge_search_results(
    result_lists: list[list[WebSearchResult]],
    *,
    k: int | None = None,
    max_per_host: int = 2,
    host_cap_top_k: int | None = None,
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
    k = settings.rrf_k if k is None else k
    start_time = time.time()
    merged: dict[str, _MergedCandidate] = {}
    encounter_order: dict[str, int] = {}
    for results in result_lists:
        for rank, result in enumerate(results, start=1):
            key = canonicalize_url(result.link)
            if key not in merged:
                merged[key] = _MergedCandidate(result=result)
                encounter_order[key] = len(encounter_order)
            bucket = merged[key]
            bucket.score += 1.0 / (k + rank)
            bucket.providers.update(provider for provider in result.providers or [] if provider)
            bucket.result = _pick_better(bucket.result, result)
    ranked = sorted(merged.items(), key=lambda item: (-item[1].score, encounter_order[item[0]]))
    ranked = _apply_host_cap(
        ranked,
        encounter_order,
        max_per_host=max_per_host,
        top_k=host_cap_top_k or len(ranked),
    )
    output = [
        bucket.result.model_copy(
            update={
                "providers": sorted(bucket.providers) or bucket.result.providers,
                "provider_count": len(bucket.providers),
                "score": bucket.score,
            }
        )
        for _, bucket in ranked
    ]
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
        k=k,
        discarded_count=discarded_count,
        overlap_rate=overlap_rate,
        duration_seconds=duration_seconds,
        max_per_host=max_per_host,
        host_cap_top_k=host_cap_top_k,
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
    if run_key:
        try:
            for rank, result in enumerate(output, start=1):
                analytics_insert_merged_candidates(
                    run_key=run_key,
                    rank=rank,
                    link=result.link,
                    title=result.title,
                    snippet=result.snippet,
                    domain=result.domain or "",
                    rrf_score=result.score or 0.0,
                    provider_count=result.provider_count,
                    providers=result.providers or [],
                    overlap_flag=canonicalize_url(result.link) in overlapping_urls,
                    payload_json={"k": k, "overlap_rate": round(overlap_rate, 4)},
                )
        except Exception as exc:
            logger.debug("analytics insert_merged_candidates failed: %s", exc)
    return output
