"""Telemetry recording helpers for AI/YouTube tools."""

from __future__ import annotations

from .attributes import (
    GEMINI_GROUNDING_CHUNKS,
    GEMINI_GROUNDING_QUERIES,
    GEMINI_STRUCTURED_OUTPUT,
    SEARCH_NUM_RESULTS_RETURNED,
    YOUTUBE_BACKEND_USED,
    YOUTUBE_DURATION_SECONDS,
    YOUTUBE_FORMAT,
    YOUTUBE_IS_TRANSLATED,
    YOUTUBE_LANGUAGE,
    YOUTUBE_SEARCH_BACKEND,
)
from .metrics import (
    get_gemini_metrics,
    get_youtube_metrics,
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
    "record_gemini_search",
        "record_youtube_search",
    "record_youtube_transcript",
]
