"""Telemetry metric singletons and accessors."""

from __future__ import annotations

from opentelemetry import metrics

from .spans import get_meter

# ============================================================================
# METRIC SINGLETONS
# ============================================================================

# Provider metrics
_provider_call_counter: metrics.Counter | None = None
_provider_duration_histogram: metrics.Histogram | None = None
_provider_results_counter: metrics.Counter | None = None

# Search metrics
_search_total_counter: metrics.Counter | None = None
_search_duration_histogram: metrics.Histogram | None = None
_search_merge_histogram: metrics.Histogram | None = None

# Cache metrics
_cache_request_counter: metrics.Counter | None = None
_cache_duration_histogram: metrics.Histogram | None = None

# MCP protocol metrics
_mcp_tool_counter: metrics.Counter | None = None
_mcp_error_counter: metrics.Counter | None = None

# Content resolution metrics
_content_resolution_counter: metrics.Counter | None = None
_content_duration_histogram: metrics.Histogram | None = None
_content_fallback_counter: metrics.Counter | None = None
_content_error_counter: metrics.Counter | None = None

# RRF merge metrics
_rrf_merge_counter: metrics.Counter | None = None
_rrf_provider_contribution_counter: metrics.Counter | None = None
_rrf_score_histogram: metrics.Histogram | None = None

# Query rewrite metrics
_rewrite_counter: metrics.Counter | None = None
_rewrite_duration_histogram: metrics.Histogram | None = None

# Reranking metrics
_rerank_counter: metrics.Counter | None = None
_rerank_duration_histogram: metrics.Histogram | None = None
_rerank_score_histogram: metrics.Histogram | None = None
_rerank_diversity_counter: metrics.Counter | None = None

# Circuit breaker metrics
_circuit_state_gauge: metrics.UpDownCounter | None = None
_circuit_event_counter: metrics.Counter | None = None

# Gemini/Perplexity metrics
_gemini_counter: metrics.Counter | None = None
_perplexity_counter: metrics.Counter | None = None

# YouTube metrics
_youtube_transcript_counter: metrics.Counter | None = None
_youtube_search_counter: metrics.Counter | None = None

# Query quality metrics (Phase 2)
_query_length_histogram: metrics.Histogram | None = None
_domain_diversity_histogram: metrics.Histogram | None = None


def get_provider_metrics() -> tuple[metrics.Counter, metrics.Histogram, metrics.Counter]:
    """Get provider metrics (call counter, duration histogram, results counter)."""
    meter = get_meter()
    global _provider_call_counter, _provider_duration_histogram, _provider_results_counter

    if _provider_call_counter is None:
        _provider_call_counter = meter.create_counter(
            name="web_search_provider_calls_total",
            description="Total provider API calls",
            unit="1",
        )

    if _provider_duration_histogram is None:
        _provider_duration_histogram = meter.create_histogram(
            name="web_search_provider_duration_seconds",
            description="Provider call latency distribution",
            unit="s",
            # Bucket boundaries: 10ms, 20ms, 50ms, 100ms, 200ms, 500ms, 1s, 2s, 5s, 10s
            explicit_bucket_boundaries_advisory=[
                0.01,
                0.02,
                0.05,
                0.1,
                0.2,
                0.5,
                1.0,
                2.0,
                5.0,
                10.0,
            ],
        )

    if _provider_results_counter is None:
        _provider_results_counter = meter.create_counter(
            name="web_search_provider_results_total",
            description="Total results returned by provider",
            unit="1",
        )

    return (
        _provider_call_counter,
        _provider_duration_histogram,
        _provider_results_counter,
    )


def get_search_metrics() -> tuple[metrics.Counter, metrics.Histogram, metrics.Histogram]:
    """Get search metrics (total counter, duration histogram, merge histogram)."""
    meter = get_meter()
    global _search_total_counter, _search_duration_histogram, _search_merge_histogram

    if _search_total_counter is None:
        _search_total_counter = meter.create_counter(
            name="web_search_requests_total",
            description="Total web_search tool invocations",
            unit="1",
        )

    if _search_duration_histogram is None:
        _search_duration_histogram = meter.create_histogram(
            name="web_search_duration_seconds",
            description="Complete search pipeline latency",
            unit="s",
            explicit_bucket_boundaries_advisory=[
                0.1,
                0.2,
                0.5,
                1.0,
                2.0,
                5.0,
                10.0,
                30.0,
                60.0,
            ],
        )

    if _search_merge_histogram is None:
        _search_merge_histogram = meter.create_histogram(
            name="web_search_merge_duration_seconds",
            description="RRF merge algorithm latency",
            unit="s",
            explicit_bucket_boundaries_advisory=[0.001, 0.002, 0.005, 0.01, 0.02, 0.05],
        )

    return _search_total_counter, _search_duration_histogram, _search_merge_histogram


def get_search_total_metric() -> metrics.Counter:
    """Get search total counter directly (convenience function for the search package)."""
    total_counter, _, _ = get_search_metrics()
    return total_counter


def get_cache_metrics() -> tuple[metrics.Counter, metrics.Histogram]:
    """Get cache metrics (request counter, duration histogram)."""
    meter = get_meter()
    global _cache_request_counter, _cache_duration_histogram

    if _cache_request_counter is None:
        _cache_request_counter = meter.create_counter(
            name="web_search_cache_requests_total",
            description="Cache lookup requests",
            unit="1",
        )

    if _cache_duration_histogram is None:
        _cache_duration_histogram = meter.create_histogram(
            name="web_search_cache_duration_seconds",
            description="Cache lookup latency",
            unit="s",
            explicit_bucket_boundaries_advisory=[0.001, 0.005, 0.01, 0.02, 0.05, 0.1],
        )

    return _cache_request_counter, _cache_duration_histogram


def get_mcp_metrics() -> tuple[metrics.Counter, metrics.Counter]:
    """Get MCP protocol metrics (tool counter, error counter)."""
    meter = get_meter()
    global _mcp_tool_counter, _mcp_error_counter

    if _mcp_tool_counter is None:
        _mcp_tool_counter = meter.create_counter(
            name="mcp_tool_invocations_total",
            description="MCP tool call count",
            unit="1",
        )

    if _mcp_error_counter is None:
        _mcp_error_counter = meter.create_counter(
            name="mcp_errors_total",
            description="MCP protocol errors",
            unit="1",
        )

    return _mcp_tool_counter, _mcp_error_counter


def get_content_metrics() -> tuple[metrics.Counter, metrics.Histogram]:
    """Get content resolution metrics."""
    meter = get_meter()
    global \
        _content_resolution_counter, \
        _content_duration_histogram, \
        _content_fallback_counter, \
        _content_error_counter

    if _content_resolution_counter is None:
        _content_resolution_counter = meter.create_counter(
            name="web_search_content_resolutions_total",
            description="Content resolution attempts by stage",
            unit="1",
        )

    if _content_duration_histogram is None:
        _content_duration_histogram = meter.create_histogram(
            name="web_search_content_duration_seconds",
            description="Content extraction latency per stage",
            unit="s",
            explicit_bucket_boundaries_advisory=[
                0.1,
                0.5,
                1.0,
                2.0,
                5.0,
                10.0,
                20.0,
                30.0,
            ],
        )

    if _content_fallback_counter is None:
        _content_fallback_counter = meter.create_counter(
            name="web_search_content_fallback_total",
            description="Content resolution fallbacks to later stages (bs4_markdownify, jina, browser)",
            unit="1",
        )

    if _content_error_counter is None:
        _content_error_counter = meter.create_counter(
            name="web_search_content_errors_total",
            description="Content resolution errors by stage",
            unit="1",
        )

    return _content_resolution_counter, _content_duration_histogram


def get_rrf_metrics() -> tuple[metrics.Counter, metrics.Counter, metrics.Histogram]:
    """Get RRF merge metrics."""
    meter = get_meter()
    global _rrf_merge_counter, _rrf_provider_contribution_counter, _rrf_score_histogram

    if _rrf_merge_counter is None:
        _rrf_merge_counter = meter.create_counter(
            name="web_search_rrf_merge_total",
            description="RRF merge operations with discarded/overlap details",
            unit="1",
        )

    if _rrf_provider_contribution_counter is None:
        _rrf_provider_contribution_counter = meter.create_counter(
            name="web_search_rrf_provider_contribution",
            description="How many final results came from each provider",
            unit="1",
        )

    if _rrf_score_histogram is None:
        _rrf_score_histogram = meter.create_histogram(
            name="web_search_rrf_score_distribution",
            description="Distribution of final RRF scores",
            unit="1",
            explicit_bucket_boundaries_advisory=[0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0],
        )

    return _rrf_merge_counter, _rrf_provider_contribution_counter, _rrf_score_histogram


def get_rewrite_metrics() -> tuple[metrics.Counter, metrics.Histogram]:
    """Get query rewrite metrics."""
    meter = get_meter()
    global _rewrite_counter, _rewrite_duration_histogram

    if _rewrite_counter is None:
        _rewrite_counter = meter.create_counter(
            name="web_search_query_rewrite_total",
            description="Query rewrite operations by policy",
            unit="1",
        )

    if _rewrite_duration_histogram is None:
        _rewrite_duration_histogram = meter.create_histogram(
            name="web_search_query_rewrite_duration_seconds",
            description="Query rewrite latency",
            unit="s",
            explicit_bucket_boundaries_advisory=[0.1, 0.2, 0.5, 1.0, 2.0, 5.0],
        )

    return _rewrite_counter, _rewrite_duration_histogram


def get_rerank_metrics() -> tuple[
    metrics.Counter, metrics.Histogram, metrics.Histogram, metrics.Counter
]:
    """Get reranking metrics."""
    meter = get_meter()
    global \
        _rerank_counter, \
        _rerank_duration_histogram, \
        _rerank_score_histogram, \
        _rerank_diversity_counter

    if _rerank_counter is None:
        _rerank_counter = meter.create_counter(
            name="web_search_rerank_total",
            description="Reranking pipeline executions by stage",
            unit="1",
        )

    if _rerank_duration_histogram is None:
        _rerank_duration_histogram = meter.create_histogram(
            name="web_search_rerank_duration_seconds",
            description="Rerank stage latency",
            unit="s",
            explicit_bucket_boundaries_advisory=[0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0],
        )

    if _rerank_score_histogram is None:
        _rerank_score_histogram = meter.create_histogram(
            name="web_search_rerank_scores",
            description="Relevance score distribution from Jina reranker (shifted +1.0 to handle negative scores)",
            unit="1",
            # Buckets for shifted range: raw scores -1.0 to 1.0 become 0.0 to 2.0
            explicit_bucket_boundaries_advisory=[
                0.0,
                0.2,
                0.4,
                0.6,
                0.8,
                1.0,
                1.2,
                1.4,
                1.6,
                1.8,
                2.0,
            ],
        )

    if _rerank_diversity_counter is None:
        _rerank_diversity_counter = meter.create_counter(
            name="web_search_rerank_diversity_removals",
            description="Results removed by diversity pruning",
            unit="1",
        )

    return (
        _rerank_counter,
        _rerank_duration_histogram,
        _rerank_score_histogram,
        _rerank_diversity_counter,
    )


def get_circuit_metrics() -> tuple[metrics.UpDownCounter, metrics.Counter]:
    """Get circuit breaker metrics."""
    meter = get_meter()
    global _circuit_state_gauge, _circuit_event_counter

    if _circuit_state_gauge is None:
        _circuit_state_gauge = meter.create_up_down_counter(
            name="web_search_provider_circuit_state",
            description="Circuit breaker state per provider (0=closed, 1=open, 0.5=half_open)",
            unit="1",
        )

    if _circuit_event_counter is None:
        _circuit_event_counter = meter.create_counter(
            name="web_search_provider_circuit_events",
            description="Circuit breaker state changes",
            unit="1",
        )

    return _circuit_state_gauge, _circuit_event_counter


def get_gemini_metrics() -> metrics.Counter:
    """Get Gemini search metrics."""
    meter = get_meter()
    global _gemini_counter

    if _gemini_counter is None:
        _gemini_counter = meter.create_counter(
            name="mcp_gemini_search_details",
            description="Gemini search specifics (grounding queries, chunks, structured output)",
            unit="1",
        )

    return _gemini_counter


def get_perplexity_metrics() -> metrics.Counter:
    """Get Perplexity search metrics."""
    meter = get_meter()
    global _perplexity_counter

    if _perplexity_counter is None:
        _perplexity_counter = meter.create_counter(
            name="mcp_perplexity_search_details",
            description="Perplexity search specifics (depth, source count, model)",
            unit="1",
        )

    return _perplexity_counter


def get_youtube_metrics() -> tuple[metrics.Counter, metrics.Counter]:
    """Get YouTube metrics."""
    meter = get_meter()
    global _youtube_transcript_counter, _youtube_search_counter

    if _youtube_transcript_counter is None:
        _youtube_transcript_counter = meter.create_counter(
            name="mcp_youtube_transcript_details",
            description="YouTube transcript specifics (format, language, duration)",
            unit="1",
        )

    if _youtube_search_counter is None:
        _youtube_search_counter = meter.create_counter(
            name="mcp_youtube_search_details",
            description="YouTube search specifics",
            unit="1",
        )

    return _youtube_transcript_counter, _youtube_search_counter


def get_query_quality_metrics() -> tuple[metrics.Histogram, metrics.Histogram]:
    """Get query quality metrics (P2-1: query length, P2-2: domain diversity).

    Returns:
        Tuple of (query_length_histogram, domain_diversity_histogram)
    """
    meter = get_meter()
    global _query_length_histogram, _domain_diversity_histogram

    if _query_length_histogram is None:
        _query_length_histogram = meter.create_histogram(
            name="web_search_query_length_chars",
            description="Distribution of query string lengths (detect keyword pile-on)",
            unit="chars",
            # Buckets: short queries (10-50 chars), medium (100 chars), long keyword pile-on (500+)
            explicit_bucket_boundaries_advisory=[10, 20, 50, 100, 200, 500],
        )

    if _domain_diversity_histogram is None:
        _domain_diversity_histogram = meter.create_histogram(
            name="web_search_domain_diversity",
            description="Unique domains in top N results (detect homogeneous results)",
            unit="domains",
            # Buckets: 1 domain (all same), 3-5 (good diversity), 10+ (excellent)
            explicit_bucket_boundaries_advisory=[1, 2, 3, 5, 7, 10, 15],
        )

    return _query_length_histogram, _domain_diversity_histogram


__all__ = [
    "get_cache_metrics",
    "get_circuit_metrics",
    "get_content_metrics",
    "get_gemini_metrics",
    "get_mcp_metrics",
    "get_perplexity_metrics",
    "get_provider_metrics",
    "get_query_quality_metrics",
    "get_rerank_metrics",
    "get_rewrite_metrics",
    "get_rrf_metrics",
    "get_search_metrics",
    "get_search_total_metric",
    "get_youtube_metrics",
]
