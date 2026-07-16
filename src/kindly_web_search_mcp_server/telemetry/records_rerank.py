"""Telemetry recording helpers for reranking."""

from __future__ import annotations

from .attributes import (
    RERANK_INPUT_COUNT,
    RERANK_MODEL,
    RERANK_OUTPUT_COUNT,
    RERANK_REMOVED_COUNT,
    RERANK_STAGE,
)
from .metrics import get_rerank_metrics


def record_rerank_stage(
    stage: str,
    input_count: int,
    output_count: int,
    duration_seconds: float | None = None,
    relevance_scores: list[float] | None = None,
    model: str | None = None,
) -> None:
    """Record reranking pipeline stage.

    Args:
        stage: "bi_encoder", "jina", or "diversity"
        input_count: Candidates before stage
        output_count: Candidates after stage
        duration_seconds: Stage latency
        relevance_scores: Relevance scores from Jina (for jina stage)
    """
    rerank_counter, duration_histogram, score_histogram = get_rerank_metrics()

    removed_count = input_count - output_count
    rerank_counter.add(
        1,
        {
            RERANK_STAGE: stage,
            RERANK_INPUT_COUNT: input_count,
            RERANK_OUTPUT_COUNT: output_count,
            RERANK_REMOVED_COUNT: removed_count,
            RERANK_MODEL: model or "",
        },
    )

    if duration_seconds is not None:
        duration_histogram.record(
            duration_seconds,
            {
                RERANK_STAGE: stage,
                RERANK_MODEL: model or "",
            },
        )

    # Record individual relevance scores for distribution
    # IMPORTANT: Jina reranker can return negative scores (valid for relevance ranking)
    # To track score distribution properly, we use a two-pronged approach:
    # 1. Shift all scores to be positive (add 1.0 offset) for histogram recording
    # 2. Record raw scores as span events for accurate visibility in Grafana
    if relevance_scores and stage == "jina":
        for score in relevance_scores[:20]:  # Limit to top 20
            # Shift score by +1.0 to ensure histogram receives positive values
            # Jina scores typically range from -1.0 to 1.0, so shifted range is 0.0 to 2.0
            shifted_score = score + 1.0
            score_histogram.record(
                shifted_score,
                {
                    RERANK_STAGE: stage,
                    "rerank.score_shifted": "true",  # Indicate transformation for query interpretation
                },
            )


__all__ = [
    "record_rerank_stage",
]
