from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from ..embeddings import embed_texts
from ..embeddings.hf_inference import (
    CircuitOpenError,
    EmbeddingAPIError,
    EmbeddingTimeoutError,
)
from ..models import WebSearchResult
from ..settings import settings
from .diversity import maximal_marginal_relevance_rank
from .models import RerankEmbeddingContext
from .observability import record_rerank_candidate_rows_async


@dataclass(frozen=True, slots=True)
class DiversityStageOutcome:
    candidates: list[WebSearchResult]
    input_count: int
    output_count: int
    duration_seconds: float
    removed_count: int


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
    recency_weight: float = 0.15,
    half_life_days: int = 90,
    searxng_time_range: str | None = None,
) -> tuple[list[WebSearchResult], list[float], float, float]:
    if not ranked_results:
        return candidates, [], 0.0, 0.0

    sorted_ranked = sorted(ranked_results, key=lambda result: result.score, reverse=True)
    raw_scores = [result.score for result in sorted_ranked]
    normalized_scores = raw_scores if preserve_raw_scores else normalize_scores_minmax(raw_scores)
    apply_recency = searxng_time_range is None and recency_weight > 0

    for ranked_result, norm_score in zip(sorted_ranked, normalized_scores, strict=False):
        idx = ranked_result.index
        final_score = norm_score
        if apply_recency:
            final_score += recency_weight * compute_recency_score(
                candidates[idx].published_date, half_life_days
            )
        candidates[idx] = candidates[idx].model_copy(update={"score": final_score})

    candidates = [candidates[item.index] for item in sorted_ranked]
    relevance_scores = [
        result.score
        for result in candidates[: min(10, len(candidates))]
        if result.score is not None
    ]
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


async def run_diversity_pruning(
    *,
    query: str,
    query_embedding: list[float] | None,
    candidates: list[WebSearchResult],
    top_k: int,
    embedding_ctx: RerankEmbeddingContext | None,
    mmr_lambda: float,
    logger: logging.Logger,
    run_key: str | None = None,
    searxng_time_range: str | None = None,
    fetch_missing_embeddings: bool = True,
) -> DiversityStageOutcome:
    if not query_embedding:
        return DiversityStageOutcome(candidates, len(candidates), len(candidates), 0.0, 0)

    before_candidates = list(candidates)
    stage_input = candidates[: top_k * 2]
    stage_input_count = len(stage_input)
    stage_output_count = stage_input_count
    diversity_removed = 0
    stage_start = time.time()
    try:
        embeddings: list[list[float] | None] = []
        missing_indices: list[int] = []
        if embedding_ctx is not None:
            for idx, candidate in enumerate(stage_input):
                emb = embedding_ctx.find(candidate.link.strip())
                if emb is not None:
                    embeddings.append(emb.dense)
                else:
                    embeddings.append(None)
                    missing_indices.append(idx)
        else:
            missing_indices = list(range(len(stage_input)))
            embeddings = [None] * len(stage_input)

        if missing_indices and not fetch_missing_embeddings:
            logger.warning(
                "Diversity embedding fetch skipped after upstream embedding failure "
                "(missing=%d, stage_input=%d)",
                len(missing_indices),
                len(stage_input),
            )
            return DiversityStageOutcome(
                candidates,
                stage_input_count,
                stage_output_count,
                time.time() - stage_start,
                diversity_removed,
            )

        if missing_indices:
            missing_texts = [
                f"{stage_input[idx].title}\n{stage_input[idx].snippet}" for idx in missing_indices
            ]
            missing_embeddings = await embed_texts(
                missing_texts,
                timeout=settings.embedding_timeout_seconds,
            )
            for idx, emb in zip(missing_indices, missing_embeddings, strict=True):
                embeddings[idx] = emb

        # Defensive: ensure every slot was filled.
        if any(emb is None for emb in embeddings) or len(embeddings) != len(stage_input):
            logger.warning(
                "Diversity embedding mismatch: got %s, expected %s, skipping diversity stage",
                len([e for e in embeddings if e is not None]),
                len(stage_input),
            )
            return DiversityStageOutcome(
                candidates,
                stage_input_count,
                stage_output_count,
                time.time() - stage_start,
                diversity_removed,
            )

        dense_embeddings = [emb for emb in embeddings if emb is not None]
        relevance_scores = [
            candidate.score if candidate.score is not None else 0.0 for candidate in stage_input
        ]
        diversified_rank = maximal_marginal_relevance_rank(
            query_embedding,
            dense_embeddings,
            [candidate.link for candidate in stage_input],
            lambda_param=mmr_lambda,
            max_per_host=2,
            relevance_scores=relevance_scores,
        )
        candidates = [candidates[i] for i in diversified_rank[: top_k * 2]] + candidates[
            top_k * 2 :
        ]
        stage_output_count = len(candidates)
        diversity_removed = len(diversified_rank) - len(diversified_rank[: top_k * 2])
    except (EmbeddingTimeoutError, EmbeddingAPIError, CircuitOpenError, Exception) as exc:
        logger.warning("Diversity embedding failed: %s: %s", type(exc).__name__, exc)
    await record_rerank_candidate_rows_async(
        logger,
        run_key=run_key,
        stage="diversity",
        before_candidates=before_candidates,
        after_candidates=candidates,
        payload_json={
            "mmr_lambda": mmr_lambda,
            "top_k": top_k,
        },
    )
    return DiversityStageOutcome(
        candidates,
        stage_input_count,
        stage_output_count,
        time.time() - stage_start,
        diversity_removed,
    )
