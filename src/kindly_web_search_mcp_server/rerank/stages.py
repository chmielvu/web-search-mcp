from __future__ import annotations

import logging
import math
from datetime import datetime

import numpy as np

from ..models import WebSearchResult


def normalize_scores_minmax(scores: list[float]) -> list[float]:
    if not scores:
        return []
    arr = np.array(scores)
    min_s, max_s = arr.min(), arr.max()
    if max_s - min_s < 1e-9:
        return [0.5] * len(scores)
    return ((arr - min_s) / (max_s - min_s)).tolist()


def compute_recency_score(published_date: str | None, half_life_days: int = 90) -> float:
    if not published_date:
        return 0.0
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
    ranked_results: list,
    *,
    preserve_raw_scores: bool = False,
    store_cross_scores: bool = False,
    update_score: bool = True,
    recency_weight: float = 0.15,
    half_life_days: int = 90,
    searxng_time_range: str | None = None,
) -> tuple[list[WebSearchResult], list[float], float, float]:
    if not ranked_results:
        return candidates, [], 0.0, 0.0

    ordered_ranked = [
        item for item in ranked_results
        if isinstance(getattr(item, "index", None), int) and 0 <= item.index < len(candidates)
    ]
    if not ordered_ranked:
        return candidates, [], 0.0, 0.0
    raw_scores = [result.score for result in ordered_ranked]
    normalized_scores = raw_scores if preserve_raw_scores else normalize_scores_minmax(raw_scores)
    apply_recency = searxng_time_range is None and recency_weight > 0

    for ranked_result, norm_score in zip(ordered_ranked, normalized_scores, strict=True):
        idx = ranked_result.index
        final_score = norm_score
        if apply_recency:
            final_score += recency_weight * compute_recency_score(
                candidates[idx].published_date, half_life_days
            )
        updates = {}
        if update_score:
            updates["score"] = final_score
        if store_cross_scores:
            updates["cross_relevance_score"] = float(ranked_result.score)
        if updates:
            candidates[idx] = candidates[idx].model_copy(update=updates)

    candidates = [candidates[item.index] for item in ordered_ranked]
    relevance_scores = [float(item.score) for item in ordered_ranked[:10]]
    max_score = max(relevance_scores) if relevance_scores else 0.0
    avg_score = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0.0
    return candidates, relevance_scores, max_score, avg_score


def apply_entity_overlap_boost(
    candidates: list[WebSearchResult],
    *,
    query_entities: list | None,
    entity_overlap_enabled: bool,
    entity_overlap_weight: float,
    logger: logging.Logger,
    ab_weight: float | None = None,
) -> None:
    if not entity_overlap_enabled or not query_entities:
        return
    try:
        from ..entity.overlap import compute_entity_overlap

        weight = entity_overlap_weight if ab_weight is None else ab_weight
        overlaps: list[float] = []
        for candidate in candidates[: min(20, len(candidates))]:
            candidate_entities = getattr(candidate, "entities", None) or []
            overlap = compute_entity_overlap(
                query_entities,
                candidate_entities if isinstance(candidate_entities, (list, tuple)) else [],  # type: ignore[arg-type]
            )
            overlaps.append(overlap)
            if getattr(candidate, "score", None) is not None:
                candidate.score = float(candidate.score) + (weight * overlap)  # type: ignore[arg-type]
        if overlaps:
            logger.debug(
                "Entity overlap boost applied: mean=%s min=%s max=%s weight=%s",
                sum(overlaps) / len(overlaps),
                min(overlaps),
                max(overlaps),
                weight,
            )
    except Exception as exc:
        logger.debug("entity overlap rerank skipped: %s", exc)
