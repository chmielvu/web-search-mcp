from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from ..models import WebSearchResult
from ..utils.url_canonicalize import canonicalize_url
from .models import RerankResult


def normalize_scores_minmax(scores: list[float]) -> list[float]:
    """Normalize finite scores while preserving deterministic tie order."""
    if not scores:
        return []
    if any(not math.isfinite(float(score)) for score in scores):
        raise ValueError("rerank scores must be finite")
    min_score = min(scores)
    max_score = max(scores)
    if max_score - min_score < 1e-9:
        return [1.0 - index / max(len(scores) - 1, 1) for index in range(len(scores))]
    scale = max_score - min_score
    return [(float(score) - min_score) / scale for score in scores]


def compute_recency_score(published_date: str | None, half_life_days: int = 90) -> float:
    if not published_date:
        return 0.0
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    try:
        pub_dt = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
        now = datetime.now(pub_dt.tzinfo) if pub_dt.tzinfo else datetime.now()
        age_days = (now - pub_dt).days
        if age_days < 0:
            return 1.0
        return math.exp(-age_days / half_life_days)
    except (ValueError, AttributeError, TypeError):
        return 0.0


def apply_ranked_results(
    candidates: list[WebSearchResult],
    ranked_results: Sequence[RerankResult],
    *,
    stage_name: Literal["cross_encoder", "rankllm"],
    recency_weight: float = 0.15,
    half_life_days: int = 90,
) -> tuple[list[WebSearchResult], list[float], float, float]:
    """Apply a validated partial ranking and retain the untouched tail."""
    if stage_name not in {"cross_encoder", "rankllm"}:
        raise ValueError(f"unsupported rerank stage: {stage_name}")
    if (
        not isinstance(recency_weight, (int, float))
        or not math.isfinite(float(recency_weight))
        or not 0.0 <= recency_weight <= 1.0
    ):
        raise ValueError("recency_weight must be finite and in [0, 1]")

    original_candidates = list(candidates)
    candidate_keys = [canonicalize_url(candidate.link) for candidate in original_candidates]
    if len(candidate_keys) != len(set(candidate_keys)):
        raise ValueError("rerank candidates contain duplicate canonical identities")
    if not ranked_results:
        return original_candidates, [], 0.0, 0.0
    ordered_ranked: list[RerankResult] = []
    seen_indices: set[int] = set()
    for result in ranked_results:
        index = getattr(result, "index", None)
        score = getattr(result, "relevance_score", None)
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("rerank result index must be an integer")
        if not 0 <= index < len(original_candidates):
            raise ValueError(f"rerank result index out of range: {index}")
        if index in seen_indices:
            raise ValueError(f"rerank result contains duplicate index: {index}")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("rerank result score must be numeric")
        if not math.isfinite(float(score)):
            raise ValueError("rerank result score must be finite")
        seen_indices.add(index)
        ordered_ranked.append(result)

    raw_scores = [float(result.relevance_score) for result in ordered_ranked]
    normalized_scores = normalize_scores_minmax(raw_scores)
    scored_updates: list[tuple[float, int, int, WebSearchResult]] = []
    for rank_position, (ranked_result, normalized_score) in enumerate(
        zip(ordered_ranked, normalized_scores, strict=True)
    ):
        candidate = original_candidates[ranked_result.index]
        if stage_name == "cross_encoder":
            recency_score = compute_recency_score(candidate.published_date, half_life_days)
            final_score = (1.0 - recency_weight) * normalized_score + recency_weight * recency_score
            update = {
                "cross_encoder_score": float(ranked_result.relevance_score),
                "recency_score": recency_score,
                "final_score": final_score,
            }
        else:
            final_score = normalized_score
            update = {
                "rankllm_score": float(ranked_result.relevance_score),
                "final_score": final_score,
            }
        scored_updates.append(
            (final_score, rank_position, ranked_result.index, candidate.model_copy(update=update))
        )

    if stage_name == "cross_encoder":
        scored_updates.sort(key=lambda item: (-item[0], item[1]))
    ordered_indices = [item[2] for item in scored_updates]
    updated_by_index = {item[2]: item[3] for item in scored_updates}
    ordered = [updated_by_index[index] for index in ordered_indices]
    ordered.extend(
        original_candidates[index]
        for index in range(len(original_candidates))
        if index not in seen_indices
    )
    max_score = max(raw_scores)
    avg_score = sum(raw_scores) / len(raw_scores)
    return ordered, raw_scores, max_score, avg_score
