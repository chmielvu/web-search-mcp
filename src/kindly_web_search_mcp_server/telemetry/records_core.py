"""Telemetry recording helpers for core search operations."""

from __future__ import annotations

from .attributes import (
    CACHE_HIT,
    CACHE_TYPE,
    ERROR_TYPE,
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_NAME,
    HTTP_RESPONSE_STATUS_CODE,
    PROVIDER_ERROR_TYPE,
    PROVIDER_NAME,
    PROVIDER_STATUS,
    RESULT_POSITION,
    REWRITE_HAS_PRECISION_SIGNALS,
    REWRITE_MODEL,
    REWRITE_POLICY,
    REWRITE_VARIANT_COUNT,
    RRF_DISCARDED_COUNT,
    RRF_INPUT_LISTS,
    RRF_INPUT_TOTAL,
    RRF_OUTPUT_TOTAL,
    RRF_OVERLAP_RATE,
    SEARCH_NUM_RESULTS_RETURNED,
    SEARCH_PROVIDERS_USED,
    STATUS_ERROR,
    STATUS_SUCCESS,
)
from .metrics import (
    get_cache_metrics,
    get_mcp_metrics,
    get_provider_metrics,
    get_query_quality_metrics,
    get_rewrite_metrics,
    get_rrf_metrics,
    get_search_metrics,
)


def record_provider_call(
    provider: str,
    duration_seconds: float,
    result_count: int,
    status_code: int = 200,
    error_type: str | None = None,
) -> None:
    """Record provider call metrics with semantic conventions.

    Args:
        provider: Provider name (searxng, ddg, gemini, tavily, brave, jina)
        duration_seconds: Call duration in seconds
        result_count: Number of results returned
        status_code: HTTP status code (200, 500, 503, etc.)
        error_type: Error type if failed (HTTP_500, TimeoutError, RateLimitError, etc.)
    """
    call_counter, duration_histogram, results_counter = get_provider_metrics()

    # Determine status from HTTP code
    status = STATUS_SUCCESS if status_code < 400 else STATUS_ERROR

    # Record call count with full attributes
    call_counter.add(
        1,
        {
            PROVIDER_NAME: provider,
            HTTP_RESPONSE_STATUS_CODE: status_code,
            PROVIDER_STATUS: status,
            PROVIDER_ERROR_TYPE: error_type or "",
        },
    )

    # Record duration
    duration_histogram.record(
        duration_seconds,
        {
            PROVIDER_NAME: provider,
            HTTP_RESPONSE_STATUS_CODE: status_code,
        },
    )

    # Record results (only meaningful on success)
    if status == STATUS_SUCCESS and result_count > 0:
        results_counter.add(result_count, {PROVIDER_NAME: provider})


def record_cache_lookup(cache_type: str, hit: bool, duration_seconds: float | None = None) -> None:
    """Record cache hit/miss and optional latency.

    Args:
        cache_type: "exact", "semantic", or "page"
        hit: True if cache hit, False if miss
        duration_seconds: Optional lookup duration
    """
    request_counter, duration_histogram = get_cache_metrics()

    request_counter.add(
        1,
        {
            CACHE_TYPE: cache_type,
            CACHE_HIT: str(hit).lower(),
        },
    )

    if duration_seconds is not None:
        duration_histogram.record(
            duration_seconds,
            {
                CACHE_TYPE: cache_type,
                CACHE_HIT: str(hit).lower(),
            },
        )


def record_search_request(
    providers_used: list[str],
    duration_seconds: float,
    result_count: int,
) -> None:
    """Record complete search operation."""
    total_counter, duration_histogram, _ = get_search_metrics()

    providers_str = str(providers_used)

    total_counter.add(1, {SEARCH_PROVIDERS_USED: providers_str})
    duration_histogram.record(duration_seconds, {SEARCH_PROVIDERS_USED: providers_str})


def record_merge(duration_seconds: float, input_lists: int, output_count: int) -> None:
    """Record RRF merge metrics."""
    _, _, merge_histogram = get_search_metrics()
    merge_histogram.record(
        duration_seconds,
        {
            "merge.input_lists": input_lists,
            "merge.output_count": output_count,
        },
    )


def record_mcp_tool_call(tool_name: str, success: bool) -> None:
    """Record MCP tool invocation."""
    tool_counter, error_counter = get_mcp_metrics()

    status = STATUS_SUCCESS if success else STATUS_ERROR
    tool_counter.add(
        1,
        {
            GEN_AI_TOOL_NAME: tool_name,
            PROVIDER_STATUS: status,
        },
    )

    if not success:
        error_counter.add(
            1,
            {
                GEN_AI_TOOL_NAME: tool_name,
                ERROR_TYPE: "tool_execution_error",
            },
        )


def record_rrf_merge(
    input_lists: int,
    input_total: int,
    output_total: int,
    discarded_count: int,
    overlap_rate: float,
    provider_contributions: dict[str, int],
) -> None:
    """Record RRF merge operation details.

    Args:
        input_lists: Number of provider result lists merged
        input_total: Total results before deduplication
        output_total: Final results after merge
        discarded_count: URLs discarded as duplicates
        overlap_rate: Fraction of URLs appearing in multiple lists
        provider_contributions: Dict of provider_name -> count of results in final top-N
    """
    merge_counter, contribution_counter, score_histogram = get_rrf_metrics()

    # Record merge operation
    merge_counter.add(
        1,
        {
            RRF_INPUT_LISTS: input_lists,
            RRF_INPUT_TOTAL: input_total,
            RRF_OUTPUT_TOTAL: output_total,
            RRF_DISCARDED_COUNT: discarded_count,
            RRF_OVERLAP_RATE: round(overlap_rate, 3),
        },
    )

    # Record per-provider contribution
    for provider, count in provider_contributions.items():
        contribution_counter.add(
            count,
            {
                PROVIDER_NAME: provider,
            },
        )


def record_rrf_score(score: float, position: int) -> None:
    """Record individual RRF score for distribution analysis."""
    _, _, score_histogram = get_rrf_metrics()
    score_histogram.record(
        score,
        {
            RESULT_POSITION: position,
        },
    )


def record_query_rewrite(
    policy: str,
    variant_count: int,
    has_precision_signals: bool,
    duration_seconds: float | None = None,
    model: str = "mistral-small-2603",
) -> None:
    """Record query rewrite operation.

    Args:
        policy: "bypass", "light_rewrite", or "expand"
        variant_count: Number of query variants produced (1-3)
        has_precision_signals: True if precision signals detected (code patterns, exact phrases)
        duration_seconds: Duration of rewrite operation
        model: Mistral model used
    """
    rewrite_counter, rewrite_histogram = get_rewrite_metrics()

    rewrite_counter.add(
        1,
        {
            REWRITE_POLICY: policy,
            REWRITE_VARIANT_COUNT: variant_count,
            REWRITE_HAS_PRECISION_SIGNALS: str(has_precision_signals).lower(),
            REWRITE_MODEL: model,
        },
    )

    if duration_seconds is not None:
        rewrite_histogram.record(
            duration_seconds,
            {
                REWRITE_POLICY: policy,
            },
        )


def record_query_length(
    query_length: int,
    policy: str,
) -> None:
    """Record query length for detecting keyword pile-on patterns.

    Args:
        query_length: Length of original query in characters
        policy: Rewrite policy mode (bypass, light_rewrite, expand)
    """
    query_length_histogram, _ = get_query_quality_metrics()
    query_length_histogram.record(
        query_length,
        {
            REWRITE_POLICY: policy,
        },
    )


def record_domain_diversity(
    unique_domains: int,
    total_results: int,
    providers_used: list[str],
) -> None:
    """Record domain diversity for detecting homogeneous results.

    Args:
        unique_domains: Number of unique domains in top N results
        total_results: Total number of results returned
        providers_used: List of providers that contributed results
    """
    _, domain_diversity_histogram = get_query_quality_metrics()
    domain_diversity_histogram.record(
        unique_domains,
        {
            SEARCH_NUM_RESULTS_RETURNED: total_results,
            SEARCH_PROVIDERS_USED: str(providers_used),
        },
    )


def record_tool_details(
    tool_name: str,
    input_query_length: int | None = None,
    input_url_count: int | None = None,
    output_result_count: int | None = None,
    output_content_length: int | None = None,
    output_transcript_length: int | None = None,
) -> None:
    """Record detailed MCP tool invocation metrics."""
    tool_counter, _ = get_mcp_metrics()

    attrs: dict[str, str | int] = {
        GEN_AI_TOOL_NAME: tool_name,
        GEN_AI_OPERATION_NAME: "execute_tool",
    }

    if input_query_length is not None:
        attrs["tool.input.query_length"] = input_query_length
    if input_url_count is not None:
        attrs["tool.input.url_count"] = input_url_count
    if output_result_count is not None:
        attrs["tool.output.result_count"] = output_result_count
    if output_content_length is not None:
        attrs["tool.output.content_length"] = output_content_length
    if output_transcript_length is not None:
        attrs["tool.output.transcript_length"] = output_transcript_length

    tool_counter.add(1, attrs)


__all__ = [
    "record_cache_lookup",
    "record_domain_diversity",
    "record_mcp_tool_call",
    "record_merge",
    "record_provider_call",
    "record_query_length",
    "record_query_rewrite",
    "record_rrf_merge",
    "record_rrf_score",
    "record_search_request",
    "record_tool_details",
]
