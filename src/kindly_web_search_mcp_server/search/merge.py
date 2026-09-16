from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ..models import WebSearchResult
from ..utils.url_canonicalize import canonicalize_url


@dataclass
class _MergedCandidate:
    result: WebSearchResult
    score: float = 0.0
    providers: set[str] = field(default_factory=set)


def _pick_better(base: WebSearchResult, candidate: WebSearchResult) -> WebSearchResult:
    return candidate if len(candidate.snippet or "") > len(base.snippet or "") else base


def memoize_canonicalize(
    canonicalize: Callable[[str], str],
) -> Callable[[str], str]:
    """Return a request-local memoizing wrapper around `canonicalize`.

    Caps each distinct raw URL to one canonicalization regardless of how
    many call sites touch it. Callers that already share a memoized
    callable (e.g. `search/ranking.py`) pass it through directly to
    avoid double-wrapping.
    """
    cache: dict[str, str] = {}

    def _call(raw_url: str) -> str:
        if raw_url in cache:
            return cache[raw_url]
        value = canonicalize(raw_url)
        cache[raw_url] = value
        return value

    return _call


def reciprocal_rank_fusion(
    result_lists: Sequence[Sequence[WebSearchResult]],
    *,
    k: int = 60,
    canonicalize: Callable[[str], str] | None = None,
    weights: Sequence[float] | None = None,
) -> list[tuple[WebSearchResult, float]]:
    """Merge ranked lists using (optionally weighted) Reciprocal Rank Fusion.

    When `canonicalize` is None, an internal memoizing wrapper around
    `canonicalize_url` is used so repeated raw URLs are canonicalized
    only once per call. Callers that already share a memoized callable
    across multiple stages should pass it in to avoid double-wrapping.

    `weights` scales each list's contribution as `weight / (k + rank)`
    instead of the classic unweighted `1 / (k + rank)`, so a scarce,
    strong signal (e.g. a paid semantic search branch) can outweigh a
    high-volume, weaker one (e.g. a free-tier engine) without changing
    how many lists either side contributes. Defaults to 1.0 for every
    list, which reproduces the classic unweighted formula exactly.
    """
    key_for = canonicalize if canonicalize is not None else memoize_canonicalize(canonicalize_url)
    resolved_weights = list(weights) if weights is not None else [1.0] * len(result_lists)
    if len(resolved_weights) != len(result_lists):
        raise ValueError(
            f"weights length ({len(resolved_weights)}) must match result_lists length "
            f"({len(result_lists)})."
        )
    merged: dict[str, _MergedCandidate] = {}
    encounter_order: dict[str, int] = {}
    for list_index, results in enumerate(result_lists):
        list_weight = resolved_weights[list_index]
        seen_in_list: set[str] = set()
        for rank, result in enumerate(results, start=1):
            key = key_for(result.link)
            if key in seen_in_list:
                continue
            seen_in_list.add(key)
            if key not in merged:
                merged[key] = _MergedCandidate(result=result, providers=set(result.providers or []))
                encounter_order[key] = len(encounter_order)
            bucket = merged[key]
            bucket.score += list_weight / (k + rank)
            bucket.providers.update(provider for provider in result.providers or [] if provider)
            bucket.result = _pick_better(bucket.result, result)

    ranked = sorted(merged.items(), key=lambda item: (-item[1].score, encounter_order[item[0]]))
    return [
        (
            bucket.result.model_copy(
                update={
                    "providers": sorted(bucket.providers) or bucket.result.providers,
                    "retrieval_rrf_score": bucket.score,
                }
            ),
            bucket.score,
        )
        for _, bucket in ranked
    ]
