"""Core reranking orchestration pipeline."""

from __future__ import annotations

import logging
import time
from typing import Any

from ..embeddings import embed_query, embed_texts
from ..embeddings.hf_inference import EmbeddingAPIError, EmbeddingTimeoutError
from ..models import WebSearchResult
from ..settings import settings
from ..telemetry import (
    record_rerank_stage,
    record_diversity_removal,
    RERANK_STAGE,
    RERANK_INPUT_COUNT,
    RERANK_OUTPUT_COUNT,
    SEARCH_QUERY,
)
from .bi_encoder import bi_encoder_filter
from .diversity import compute_embedding_diversity, maximal_marginal_relevance_rank
from .jina import jina_rerank
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


async def rerank_results(
    query: str,
    candidates: list[WebSearchResult],
    top_k: int = 10,
) -> list[WebSearchResult]:
    """
    Rerank web search results with multi-stage pipeline.

    Pipeline stages:
    1. Bi-encoder filtering (when candidates > top_k * 2)
       - Filter using embedding similarity down to top_k * 2 candidates
         so the cross-encoder (Stage 2) has a richer pool to reorder.
    2. Jina API reranking
       - Use jina-reranker-v3 by default
    3. Diversity pruning
       - Remove near-duplicates using embedding similarity

    The query embedding is computed once and shared across Stage 1 and
    Stage 3 to avoid redundant network calls.

    Args:
        query: Original search query
        candidates: List of web search results to rerank
        top_k: Final number of results to return

    Returns:
        Reranked list of web search results (max top_k items).
        Returns original candidates[:top_k] on pipeline failures (graceful degradation).
    """
    if not candidates:
        return []

    if len(candidates) <= top_k:
        logger.debug(f"Candidates ({len(candidates)}) <= top_k ({top_k}), skipping rerank")
        return candidates

    original_count = len(candidates)
    logger.info(f"Starting rerank pipeline: {original_count} candidates, target top_k={top_k}")

    # Compute query embedding once here. Both Stage 1 (bi-encoder) and
    # Stage 3 (MMR diversity) consume this vector — there is no second call.
    # If this fails, both embedding-dependent stages are skipped gracefully.
    query_embedding: list[float] | None = None
    try:
        query_embedding = await embed_query(query, timeout=30.0)
    except (EmbeddingTimeoutError, EmbeddingAPIError, Exception) as e:
        logger.warning(
            f"Query embedding failed: {type(e).__name__}: {e}; "
            "Stage 1 (bi-encoder) and Stage 3 (diversity) will be skipped"
        )

    # Create main rerank span
    with tracer.start_as_current_span(
        "rerank.pipeline",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            SEARCH_QUERY: query[:500],
            RERANK_INPUT_COUNT: original_count,
            "rerank.top_k": top_k,
        },
    ) as main_span:

        # Stage 1: Bi-encoder filtering (only if many candidates and embedding available).
        # Filter to top_k * 2 to give the cross-encoder (Stage 2) a wider pool.
        stage1_output_count = original_count
        stage1_duration = 0.0
        if query_embedding and len(candidates) > top_k * 2:
            bi_encoder_top_k = top_k * 2
            logger.debug(f"Stage 1: Bi-encoder filtering ({len(candidates)} > {top_k * 2}), keeping {bi_encoder_top_k}")
            stage1_start = time.time()
            try:
                candidates = await bi_encoder_filter(
                    query_embedding,
                    candidates,
                    top_k=bi_encoder_top_k,
                )
                stage1_output_count = len(candidates)
            except Exception as e:
                logger.warning(f"Bi-encoder filter failed: {type(e).__name__}: {e}, using top_k*2 slice")
                candidates = candidates[:bi_encoder_top_k]
                stage1_output_count = len(candidates)
            stage1_duration = time.time() - stage1_start
            logger.debug(f"After bi-encoder: {len(candidates)} candidates")

            # Record Stage 1 telemetry
            record_rerank_stage(
                stage="bi_encoder",
                input_count=original_count,
                output_count=stage1_output_count,
                duration_seconds=stage1_duration,
            )

            main_span.add_event("rerank.bi_encoder", attributes={
                RERANK_STAGE: "bi_encoder",
                RERANK_INPUT_COUNT: original_count,
                RERANK_OUTPUT_COUNT: stage1_output_count,
            })

        # Stage 2: Jina reranking
        logger.debug("Stage 2: Jina reranking")
        stage2_input_count = len(candidates)
        stage2_output_count = len(candidates)
        stage2_duration = 0.0
        relevance_scores: list[float] = []

        documents = [
            f"{candidate.title}\n{candidate.snippet}"
            for candidate in candidates
        ]

        stage2_start = time.time()
        try:
            ranked_indices = await jina_rerank(query, documents, timeout=30.0)
            sorted_ranked = sorted(ranked_indices, key=lambda x: x[1], reverse=True)
            candidates = [candidates[idx] for idx, _ in sorted_ranked]
            relevance_scores = [score for _, score in sorted_ranked[:min(10, len(sorted_ranked))]]
            stage2_output_count = len(candidates)
        except Exception as e:
            logger.warning(f"Jina rerank failed: {type(e).__name__}: {e}, skipping rerank stage")
            # Fall through to diversity pruning with current order
        stage2_duration = time.time() - stage2_start

        logger.debug(f"After Jina rerank: {len(candidates)} candidates")

        # Record Stage 2 telemetry
        if relevance_scores:
            record_rerank_stage(
                stage="jina",
                input_count=stage2_input_count,
                output_count=stage2_output_count,
                duration_seconds=stage2_duration,
                relevance_scores=relevance_scores,
            )

            main_span.add_event("rerank.jina", attributes={
                RERANK_STAGE: "jina",
                RERANK_INPUT_COUNT: stage2_input_count,
                RERANK_OUTPUT_COUNT: stage2_output_count,
                "rerank.top_score": round(max(relevance_scores), 4) if relevance_scores else 0,
                "rerank.avg_score": round(sum(relevance_scores) / len(relevance_scores), 4) if relevance_scores else 0,
            })

        # Stage 3: Diversity pruning (requires query embedding for MMR).
        logger.debug("Stage 3: Diversity pruning")
        stage3_input_count = len(candidates[: top_k * 2])
        stage3_output_count = stage3_input_count
        stage3_duration = 0.0
        diversity_removed = 0

        if query_embedding:
            texts = [
                f"{candidate.title}\n{candidate.snippet}"
                for candidate in candidates[: top_k * 2]
            ]
            stage3_start = time.time()
            try:
                embeddings = await embed_texts(texts, timeout=60.0)
                if embeddings and len(embeddings) == len(candidates[: top_k * 2]):
                    scoped_count = len(candidates[: top_k * 2])
                    scoped_urls = [candidate.link for candidate in candidates[: top_k * 2]]

                    diversified_rank = maximal_marginal_relevance_rank(
                        query_embedding,
                        embeddings,
                        scoped_urls,
                        lambda_param=settings.mmr_lambda_param,
                        max_per_host=2,
                    )

                    kept_indices, removed_scores = compute_embedding_diversity(
                        embeddings, threshold=settings.diversity_threshold
                    )
                    duplicate_keep_set = set(kept_indices)
                    kept_indices_final = [idx for idx in diversified_rank if idx in duplicate_keep_set]

                    original_indices = list(range(scoped_count))

                    # Track removed items for telemetry with actual similarity scores
                    removed_indices = set(original_indices) - set(kept_indices_final)
                    diversity_removed = len(removed_indices)

                    for removed_idx in removed_indices:
                        actual_score = removed_scores.get(removed_idx, settings.diversity_threshold)
                        record_diversity_removal(similarity_score=actual_score, threshold=settings.diversity_threshold)

                    candidates = [candidates[i] for i in kept_indices_final] + candidates[top_k * 2 :]
                    stage3_output_count = len(candidates)
                    logger.debug(f"After diversity pruning: {len(candidates)} candidates")
                else:
                    logger.warning(
                        f"Diversity embedding mismatch: got {len(embeddings) if embeddings else 0}, "
                        f"expected {len(candidates[: top_k * 2])}, skipping diversity stage"
                    )
            except (EmbeddingTimeoutError, EmbeddingAPIError, Exception) as e:
                logger.warning(f"Diversity embedding failed: {type(e).__name__}: {e}, skipping diversity stage")
            stage3_duration = time.time() - stage3_start

        # Record Stage 3 telemetry
        record_rerank_stage(
            stage="diversity",
            input_count=stage3_input_count,
            output_count=stage3_output_count,
            duration_seconds=stage3_duration,
        )

        main_span.add_event("rerank.diversity", attributes={
            RERANK_STAGE: "diversity",
            RERANK_INPUT_COUNT: stage3_input_count,
            RERANK_OUTPUT_COUNT: stage3_output_count,
            "rerank.removed_count": diversity_removed,
        })

        # Return final top_k results
        final_results = candidates[:top_k]

        main_span.set_attribute("rerank.final_count", len(final_results))

        logger.info(
            f"Rerank pipeline complete: {original_count} -> {len(final_results)} results"
        )

        return final_results
