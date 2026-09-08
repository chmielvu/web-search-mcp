"""Relevance-sensitive maximal marginal relevance selection."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..settings import settings
from .utils import normalize_scores_minmax


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
    diversity_penalties: tuple[float, ...] = ()


def select_mmr_slate(
    embeddings: Sequence[Sequence[float]],
    urls: Sequence[str],
    *,
    output_size: int,
    relevance_scores: Sequence[float],
    lambda_param: float | None = None,
    max_per_host: int | None = None,
) -> DiverseSlate:
    """Select exactly ``output_size`` items, then return residual indexes."""
    if not embeddings:
        raise ValueError("embeddings must not be empty")
    if isinstance(output_size, bool) or not isinstance(output_size, int):
        raise ValueError("output_size must be an integer")
    if not 1 <= output_size <= len(embeddings):
        raise ValueError("output_size must be in [1, len(embeddings)]")
    if len(embeddings) != len(urls):
        raise ValueError("embeddings and urls must have equal lengths")
    if len(relevance_scores) != len(embeddings):
        raise ValueError("relevance_scores and embeddings must have equal lengths")

    dimensions = len(embeddings[0])
    if dimensions == 0:
        raise ValueError("embeddings must not be empty vectors")
    normalized_embeddings: list[tuple[float, ...]] = []
    for embedding in embeddings:
        if len(embedding) != dimensions:
            raise ValueError("all embeddings must have equal dimensions")
        vector = tuple(float(value) for value in embedding)
        if any(not math.isfinite(value) for value in vector):
            raise ValueError("embeddings must contain finite values")
        normalized_embeddings.append(vector)

    resolved_lambda = settings.mmr_lambda_param if lambda_param is None else lambda_param
    if not isinstance(resolved_lambda, (int, float)) or not math.isfinite(resolved_lambda):
        raise ValueError("lambda_param must be finite")
    if not 0.0 <= resolved_lambda <= 1.0:
        raise ValueError("lambda_param must be in [0, 1]")
    resolved_host_cap = settings.diversity_max_per_host if max_per_host is None else max_per_host
    if isinstance(resolved_host_cap, bool) or not isinstance(resolved_host_cap, int):
        raise ValueError("max_per_host must be an integer")
    if resolved_host_cap < 1:
        raise ValueError("max_per_host must be at least 1")

    resolved_relevance = normalize_scores_minmax([float(score) for score in relevance_scores])

    max_pairwise = 0.0
    for left in range(len(normalized_embeddings)):
        for right in range(left + 1, len(normalized_embeddings)):
            max_pairwise = max(
                max_pairwise,
                _cosine(normalized_embeddings[left], normalized_embeddings[right]),
            )

    host_counts: dict[str, int] = {}
    for url in urls:
        host = normalized_host(url)
        host_counts[host] = host_counts.get(host, 0) + 1
    host_overflow = sum(max(0, count - resolved_host_cap) for count in host_counts.values())

    target_size = output_size
    selected: list[int] = []
    remaining = list(range(len(embeddings)))
    selected_hosts: dict[str, int] = {}
    selection_penalties: dict[int, float] = {}

    while remaining and len(selected) < target_size:
        eligible = [
            index
            for index in remaining
            if selected_hosts.get(normalized_host(urls[index]), 0) < resolved_host_cap
        ]
        if not eligible:
            # A hard host cap must not violate the requested result count.
            eligible = remaining

        best_index = max(
            eligible,
            key=lambda index: (
                resolved_lambda * resolved_relevance[index]
                - (1.0 - resolved_lambda)
                * (
                    max(
                        (
                            _cosine(normalized_embeddings[index], normalized_embeddings[chosen])
                            for chosen in selected
                        ),
                        default=0.0,
                    )
                ),
                -index,
            ),
        )
        redundancy = max(
            (
                _cosine(normalized_embeddings[best_index], normalized_embeddings[chosen])
                for chosen in selected
            ),
            default=0.0,
        )
        selection_penalties[best_index] = (1.0 - resolved_lambda) * redundancy
        selected.append(best_index)
        remaining.remove(best_index)
        host = normalized_host(urls[best_index])
        selected_hosts[host] = selected_hosts.get(host, 0) + 1

    selected_set = set(selected)
    residual = [index for index in range(len(embeddings)) if index not in selected_set]
    penalties = tuple(
        selection_penalties.get(
            index,
            (1.0 - resolved_lambda)
            * max(
                (
                    _cosine(normalized_embeddings[index], normalized_embeddings[chosen])
                    for chosen in selected
                    if chosen != index
                ),
                default=0.0,
            ),
        )
        for index in range(len(embeddings))
    )
    return DiverseSlate(
        selected_indices=tuple(selected + residual),
        triggered=target_size > 0,
        max_pairwise_similarity=max_pairwise,
        host_overflow_count=host_overflow,
        diversity_penalties=penalties,
    )
