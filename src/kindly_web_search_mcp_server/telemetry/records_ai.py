"""Telemetry recording helpers for AI/YouTube tools."""

from __future__ import annotations

from .attributes import (
    ERROR_TYPE,
    GEMINI_GROUNDING_CHUNKS,
    GEMINI_GROUNDING_QUERIES,
    GEMINI_STRUCTURED_OUTPUT,
    GEN_AI_TOOL_NAME,
    PERPLEXITY_DEPTH,
    PERPLEXITY_MODEL,
    PERPLEXITY_SOURCE_COUNT,
    PROVIDER_STATUS,
    SEARCH_NUM_RESULTS_RETURNED,
    STATUS_ERROR,
    STATUS_SUCCESS,
    YOUTUBE_BACKEND_USED,
    YOUTUBE_DURATION_SECONDS,
    YOUTUBE_FORMAT,
    YOUTUBE_IS_TRANSLATED,
    YOUTUBE_LANGUAGE,
    YOUTUBE_SEARCH_BACKEND,
)
from .metrics import (
    get_gemini_metrics,
    get_mcp_metrics,
    get_perplexity_metrics,
    get_youtube_metrics,
)


def record_agentic_research(
    *,
    depth: str,
    model: str,
    success: bool,
    sources_count: int = 0,
    tool_calls_count: int = 0,
    uncertainties_count: int = 0,
    duration_seconds: float = 0.0,
    run_limit: int = 0,
) -> None:
    """Record agentic ReAct research invocation (Layer 3 signals).

    Feeds MCP metrics + custom agentic counters/histograms. The structured
    completion event is emitted by the agent runner so the payload shape can
    stay canonical (`agentic.research.completed`) while this helper remains
    metrics-only.
    """
    # Reuse mcp counters for the tool itself (agentic_web_research)
    tool_counter, error_counter = get_mcp_metrics()
    status = STATUS_SUCCESS if success else STATUS_ERROR
    tool_counter.add(
        1,
        {
            GEN_AI_TOOL_NAME: "agentic_web_research",
            PROVIDER_STATUS: status,
            "agent.depth": depth,
            "agent.model": model[:100],  # truncate
        },
    )
    if not success:
        error_counter.add(
            1,
            {
                GEN_AI_TOOL_NAME: "agentic_web_research",
                ERROR_TYPE: "tool_execution_error",
            },
        )


def record_gemini_search(
    grounding_queries: int,
    grounding_chunks: int,
    structured_output: bool,
    duration_seconds: float | None = None,
) -> None:
    """Record Gemini search specifics."""
    gemini_counter = get_gemini_metrics()
    gemini_counter.add(
        1,
        {
            GEMINI_GROUNDING_QUERIES: grounding_queries,
            GEMINI_GROUNDING_CHUNKS: grounding_chunks,
            GEMINI_STRUCTURED_OUTPUT: str(structured_output).lower(),
        },
    )


def record_perplexity_search(
    depth: str,
    source_count: int,
    model: str = "sonar",
    duration_seconds: float | None = None,
) -> None:
    """Record Perplexity search specifics."""
    perplexity_counter = get_perplexity_metrics()
    perplexity_counter.add(
        1,
        {
            PERPLEXITY_DEPTH: depth,
            PERPLEXITY_SOURCE_COUNT: source_count,
            PERPLEXITY_MODEL: model,
        },
    )


def record_youtube_transcript(
    format: str,
    language: str,
    is_translated: bool,
    duration_seconds: int | None = None,
    backend_used: str = "api",
) -> None:
    """Record YouTube transcript specifics."""
    transcript_counter, _ = get_youtube_metrics()
    transcript_counter.add(
        1,
        {
            YOUTUBE_FORMAT: format,
            YOUTUBE_LANGUAGE: language,
            YOUTUBE_IS_TRANSLATED: str(is_translated).lower(),
            YOUTUBE_DURATION_SECONDS: duration_seconds or 0,
            YOUTUBE_BACKEND_USED: backend_used,
        },
    )


def record_youtube_search(
    num_results: int,
    duration_seconds: float | None = None,
    search_backend: str = "searxng",
) -> None:
    """Record YouTube search specifics."""
    _, search_counter = get_youtube_metrics()
    search_counter.add(
        1,
        {
            SEARCH_NUM_RESULTS_RETURNED: num_results,
            YOUTUBE_SEARCH_BACKEND: search_backend,
        },
    )


__all__ = [
    "record_agentic_research",
    "record_gemini_search",
    "record_perplexity_search",
    "record_youtube_search",
    "record_youtube_transcript",
]
