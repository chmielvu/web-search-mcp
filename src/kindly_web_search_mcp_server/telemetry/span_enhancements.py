"""Telemetry span enhancement helpers."""

from __future__ import annotations

from typing import Any

from opentelemetry import trace

from .records_core import record_rrf_score
from .attributes import (
    ERROR_TYPE,
    PROVIDER_NAME,
    RERANK_INPUT_COUNT,
    RERANK_RELEVANCE_SCORE,
    RERANK_STAGE,
    RESULT_DOMAIN,
    RESULT_HAS_SNIPPET,
    RESULT_POSITION,
    RESULT_PROVIDER_COUNT,
    RESULT_PROVIDER_SOURCES,
    RESULT_RRF_SCORE,
    RESULT_TITLE,
    RESULT_URL,
    REWRITE_VARIANT_TEXT,
    REWRITE_VARIANT_TYPE,
    RRF_DISCARDED_COUNT,
    RRF_PROVIDER_CONTRIBUTION,
    SEARCH_NUM_RESULTS_RETURNED,
)

# ============================================================================
# SPAN ENHANCEMENT FUNCTIONS
# ============================================================================


def add_results_to_span(
    span: trace.Span,
    results: list[Any],
    max_results: int = 10,
    include_rrf_details: bool = False,
) -> None:
    """Add search results to span as events for Grafana trace view.

    Each result becomes a span event with title/link visible in Grafana.
    When include_rrf_details=True, also includes RRF score and provider sources.

    Args:
        span: The span to add events to
        results: List of result objects (must have title, link attributes)
        max_results: Maximum number of results to add as events (default 10)
        include_rrf_details: If True, include RRF score and providers for each result
    """
    from urllib.parse import urlparse

    span.set_attribute(SEARCH_NUM_RESULTS_RETURNED, len(results))

    for i, r in enumerate(results[:max_results]):
        title = getattr(r, "title", str(r))[:200] if hasattr(r, "title") else str(r)[:200]
        link = getattr(r, "link", "") if hasattr(r, "link") else ""
        snippet_len = len(getattr(r, "snippet", "")) if hasattr(r, "snippet") else 0

        # Extract domain
        domain = ""
        if link:
            try:
                parsed = urlparse(link)
                domain = parsed.hostname or ""
            except Exception:
                pass

        # Base attributes
        attrs = {
            RESULT_TITLE: title,
            RESULT_URL: link,
            "result.snippet_length": snippet_len,
            RESULT_POSITION: i + 1,
            RESULT_HAS_SNIPPET: str(snippet_len > 0).lower(),
            RESULT_DOMAIN: domain,
        }

        # RRF details if available
        if include_rrf_details:
            rrf_score = getattr(r, "score", None)
            providers = getattr(r, "providers", None)
            provider_count = len(providers) if providers else 0

            if rrf_score is not None:
                attrs[RESULT_RRF_SCORE] = round(float(rrf_score), 4)
                # Record individual score for distribution analysis
                record_rrf_score(float(rrf_score), i + 1)

            if providers is not None:
                attrs[RESULT_PROVIDER_SOURCES] = str(providers)
                attrs[RESULT_PROVIDER_COUNT] = provider_count

        span.add_event(f"result.{i}", attributes=attrs)

    if len(results) > max_results:
        span.add_event(
            "results_truncated",
            attributes={"total_results": len(results)},
        )


def add_query_rewrite_variants_to_span(
    span: trace.Span,
    variants: list[Any],
) -> None:
    """Add query rewrite variants to span as events.

    Each variant becomes a span event visible in Grafana trace view.

    Args:
        span: The span to add events to
        variants: List of variant objects with type and text attributes
    """
    for i, v in enumerate(variants[:5]):  # Limit to 5 variants
        variant_type = getattr(v, "type", "unknown")
        variant_text = getattr(v, "text", getattr(v, "query", str(v)))

        span.add_event(
            f"rewrite.variant.{i}",
            attributes={
                REWRITE_VARIANT_TYPE: variant_type,
                REWRITE_VARIANT_TEXT: variant_text[:200],
            },
        )


def add_rrf_merge_details_to_span(
    span: trace.Span,
    provider_counts: dict[str, int],
    discarded_urls: list[str],
    overlapping_urls: list[str],
) -> None:
    """Add RRF merge details to span as events.

    Args:
        span: The span to add events to
        provider_counts: Dict of provider_name -> result count in final output
        discarded_urls: URLs that were discarded as duplicates
        overlapping_urls: URLs that appeared in multiple provider lists
    """
    # Provider contribution summary
    for provider, count in provider_counts.items():
        span.add_event(
            f"rrf.provider.{provider}",
            attributes={
                PROVIDER_NAME: provider,
                RRF_PROVIDER_CONTRIBUTION: count,
            },
        )

    # Discard summary
    span.add_event(
        "rrf.discards",
        attributes={
            RRF_DISCARDED_COUNT: len(discarded_urls),
            "rrf.discarded_urls_sample": str(discarded_urls[:3]) if discarded_urls else "",
        },
    )

    # Overlap summary
    span.add_event(
        "rrf.overlap",
        attributes={
            "rrf.overlapping_count": len(overlapping_urls),
            "rrf.overlap_urls_sample": str(overlapping_urls[:3]) if overlapping_urls else "",
        },
    )


def add_rerank_scores_to_span(
    span: trace.Span,
    scores: list[float],
    stage: str,
) -> None:
    """Add rerank relevance scores to span as events.

    Args:
        span: The span to add events to
        scores: List of relevance scores (0.0-1.0)
        stage: "bi_encoder" or "jina"
    """
    # Add top scores as individual events
    for i, score in enumerate(scores[:10]):
        span.add_event(
            f"rerank.score.{i}",
            attributes={
                RERANK_STAGE: stage,
                RERANK_RELEVANCE_SCORE: round(score, 4),
                RESULT_POSITION: i + 1,
            },
        )

    # Summary event
    if scores:
        span.add_event(
            f"rerank.{stage}.summary",
            attributes={
                RERANK_STAGE: stage,
                "rerank.min_score": round(min(scores), 4),
                "rerank.max_score": round(max(scores), 4),
                "rerank.avg_score": round(sum(scores) / len(scores), 4),
                RERANK_INPUT_COUNT: len(scores),
            },
        )


def set_span_error(span: trace.Span, error: Exception, error_type: str | None = None) -> None:
    """Record exception on span with proper error attributes."""
    span.record_exception(error)
    span.set_attribute(ERROR_TYPE, error_type or type(error).__name__)
    span.set_status(trace.StatusCode.ERROR, str(error)[:200])


def set_span_success(span: trace.Span, result_count: int | None = None) -> None:
    """Mark span as successful."""
    span.set_status(trace.StatusCode.OK)
    if result_count is not None:
        span.set_attribute(SEARCH_NUM_RESULTS_RETURNED, result_count)


__all__ = [
    "add_query_rewrite_variants_to_span",
    "add_rerank_scores_to_span",
    "add_results_to_span",
    "add_rrf_merge_details_to_span",
    "set_span_error",
    "set_span_success",
]
