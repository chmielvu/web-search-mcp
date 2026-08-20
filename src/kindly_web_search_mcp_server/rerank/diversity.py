"""Conditional MMR diversity over the final reranked slate.

Implements the classic Carbonell & Goldstein maximal marginal relevance
selection over precomputed bi-encoder embeddings, plus a per-host cap.
The stage is conditional: it only reorders when a duplicate pair exceeds
``similarity_threshold`` or a host exceeds ``max_per_host``; otherwise the
incoming order is preserved untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlsplit

from ..settings import settings


def normalized_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


@dataclass(frozen=True, slots=True)
class DiverseSlate:
    selected_indices: tuple[int, ...]
    triggered: bool
    max_pairwise_similarity: float
    host_overflow_count: int


def select_diverse_slate(
    embeddings: Sequence[Sequence[float]],
    urls: Sequence[str],
    *,
    output_size: int,
    lambda_param: float | None = None,
    similarity_threshold: float | None = None,
    max_per_host: int | None = None,
) -> DiverseSlate:
    """Greedy MMR selection over ``embeddings`` with a per-host cap.

    Returns the selected indices in MMR order. When no duplicate pair
    exceeds the similarity threshold and no host exceeds the cap, the
    incoming order is returned unchanged with ``triggered=False``.
    """
    if not embeddings:
        return DiverseSlate((), False, 0.0, 0)
    resolved_lambda = settings.mmr_lambda_param if lambda_param is None else lambda_param
    resolved_threshold = (
        settings.diversity_similarity_threshold
        if similarity_threshold is None
        else similarity_threshold
    )
    resolved_host_cap = (
        settings.diversity_max_per_host if max_per_host is None else max_per_host
    )

    # Trigger detection: duplicate pair above threshold or host overflow.
    max_pairwise = 0.0
    for i in range(len(embeddings)):
        for j in range(i + 1, len(embeddings)):
            similarity = _cosine(embeddings[i], embeddings[j])
            if similarity > max_pairwise:
                max_pairwise = similarity
    host_counts: dict[str, int] = {}
    host_overflow = 0
    for url in urls:
        host = normalized_host(url)
        host_counts[host] = host_counts.get(host, 0) + 1
    for count in host_counts.values():
        if count > resolved_host_cap:
            host_overflow += count - resolved_host_cap
    triggered = max_pairwise > resolved_threshold or host_overflow > 0
    if not triggered:
        return DiverseSlate(tuple(range(len(embeddings))), False, max_pairwise, host_overflow)

    selected: list[int] = []
    remaining = list(range(len(embeddings)))
    selected_host_counts: dict[str, int] = {}
    while remaining and len(selected) < output_size:
        best_index = -1
        best_score = -math.inf
        for candidate in remaining:
            host = normalized_host(urls[candidate])
            if selected_host_counts.get(host, 0) >= resolved_host_cap:
                continue
            relevance = 1.0
            redundancy = 0.0
            if selected:
                redundancy = max(_cosine(embeddings[candidate], embeddings[s]) for s in selected)
            score = resolved_lambda * relevance - (1.0 - resolved_lambda) * redundancy
            if score > best_score:
                best_score = score
                best_index = candidate
        if best_index < 0:
            break
        selected.append(best_index)
        remaining.remove(best_index)
        host = normalized_host(urls[best_index])
        selected_host_counts[host] = selected_host_counts.get(host, 0) + 1
    # Reconstruct the untouched tail: identities are never silently dropped.
    selected.extend(index for index in range(len(embeddings)) if index not in selected)
    return DiverseSlate(tuple(selected), True, max_pairwise, host_overflow)
