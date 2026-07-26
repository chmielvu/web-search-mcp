from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal
from uuid import uuid4

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..errors import format_tool_error
from ..models import (
    YouTubeSearchResponse,
    YouTubeSearchResultType,
    YouTubeTranscriptResponse,
    YouTubeTranscriptResultType,
)
from ..telemetry import record_youtube_search, record_youtube_transcript
from ..youtube import (
    YouTubeApiError,
    YouTubeError,
    YouTubeSearchError,
    calculate_total_duration,
    fetch_transcript_with_cache,
    format_transcript_text,
    format_transcript_timestamped,
    parse_youtube_url,
    search_youtube,
)

from ..heuristics.text_clean import clean_text_for_llm
from ..utils.observability import emit_tool_observability_event

LOGGER = logging.getLogger(__name__)


async def youtube_transcript(
    video_id_or_url: str,
    language: str | None = None,
    translate_to: str | None = None,
    output_format: Literal["text", "timestamped", "json"] = "text",
    backend: str | None = None,
    ctx: Context = CurrentContext(),
) -> YouTubeTranscriptResultType:
    """Extract captions from a YouTube video.

    When to use this tool:
    - To extract full transcript captions, timestamps, or structured segment JSON from a YouTube video.
    - You SHOULD call youtube_search first to discover valid video_id_or_url targets.

    Args:
        video_id_or_url: YouTube video ID, full URL, or short URL.
        language: Language code for captions (e.g., "en", "ja", "de").
            Auto-detected when not specified.
        translate_to: Translate captions to this language code.
        output_format: Output format — "text" (plain paragraph), "timestamped"
            ([MM:SS] prefixes), or "json" (segments array).
        backend: "auto" (try yt-dlp then legacy API), "ytdlp" (yt-dlp only),
            or "api" (legacy youtube-transcript-api only). Default: server setting.
    """
    format = output_format
    from ..settings import settings

    timeout_seconds = settings.youtube_transcript_timeout_seconds
    max_chars = settings.youtube_transcript_max_chars
    effective_backend = backend or settings.youtube_transcript_backend
    started = time.monotonic()
    tool_call_id = str(uuid4())
    emit_tool_observability_event(
        LOGGER,
        "youtube_transcript",
        "request",
        tool_call_id=tool_call_id,
        video_id_or_url=video_id_or_url,
        language=language,
        translate_to=translate_to,
        output_format=format,
        backend=effective_backend,
    )

    try:
        # Parse URL/ID
        target = parse_youtube_url(video_id_or_url)
        video_id = target.video_id
        canonical_url = target.canonical_url

        await ctx.report_progress(progress=20, total=100, message="Fetching transcript...")

        # Fetch transcript (cache-first, then cascade backends)
        segments, backend_used = await asyncio.wait_for(
            asyncio.to_thread(
                fetch_transcript_with_cache,
                video_id,
                language=language,
                translate_to=translate_to,
                backend=effective_backend,
            ),
            timeout=timeout_seconds,
        )

        # Determine language and translation status
        actual_language = language or "en"
        is_translated = bool(translate_to)

        # Format output
        if format == "json":
            transcript_text = ""
        elif format == "timestamped":
            transcript_text = format_transcript_timestamped(segments)
        else:
            transcript_text = format_transcript_text(segments)

        if transcript_text:
            transcript_text = clean_text_for_llm(transcript_text, role="transcript")

        # Truncate if needed
        if len(transcript_text) > max_chars:
            transcript_text = transcript_text[:max_chars].rstrip() + "…"
            LOGGER.info("Truncated transcript to %s chars for video %s", max_chars, video_id)

        duration_seconds = calculate_total_duration(segments)

        # Record YouTube transcript telemetry
        record_youtube_transcript(
            format=format,
            language=actual_language,
            is_translated=is_translated,
            duration_seconds=int(duration_seconds),
            backend_used=backend_used,
        )

        response = YouTubeTranscriptResponse(
            video_id=video_id,
            video_url=canonical_url,
            title=None,  # Title requires separate YouTube Data API call (Phase 2)
            transcript_text=transcript_text,
            language=actual_language,
            is_translated=is_translated,
            duration_seconds=duration_seconds,
            transcript_segments=segments if format == "json" else None,
            error=None,
        ).model_dump(exclude_none=True)

        emit_tool_observability_event(
            LOGGER,
            "youtube_transcript",
            "response",
            tool_call_id=tool_call_id,
            video_id=video_id,
            video_url=canonical_url,
            language=actual_language,
            output_format=format,
            backend=backend_used,
            transcript_text=transcript_text,
            transcript_segments=segments if format == "json" else None,
            output_count=len(segments),
            duration_ms=(time.monotonic() - started) * 1000,
        )

        await ctx.report_progress(progress=100, total=100, message="Done")
        return response  # type: ignore[return-value]

    except asyncio.TimeoutError:
        record_youtube_transcript(
            format=format,
            language=language or "en",
            is_translated=bool(translate_to),
            duration_seconds=None,
            backend_used=effective_backend,
        )
        error_msg = f"Transcript fetch timed out after {timeout_seconds}s"
        emit_tool_observability_event(
            LOGGER,
            "youtube_transcript",
            "error",
            tool_call_id=tool_call_id,
            video_id_or_url=video_id_or_url,
            backend=effective_backend,
            error_type="timeout",
            error_message=error_msg,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        return {  # type: ignore[return-value]
            "video_id": "",
            "video_url": video_id_or_url,
            "transcript_text": "",
            "language": language or "en",
            "error": error_msg,
            "isError": True,
            "error_type": "network",
            "action": "The request took too long. Try again or check network connectivity.",
        }

    except YouTubeError as e:
        record_youtube_transcript(
            format=format,
            language=language or "en",
            is_translated=bool(translate_to),
            duration_seconds=None,
            backend_used=effective_backend,
        )
        emit_tool_observability_event(
            LOGGER,
            "youtube_transcript",
            "error",
            tool_call_id=tool_call_id,
            video_id_or_url=video_id_or_url,
            backend=effective_backend,
            error_type="content",
            error_message=str(e),
            duration_ms=(time.monotonic() - started) * 1000,
        )
        return {  # type: ignore[return-value]
            "video_id": "",
            "video_url": video_id_or_url,
            "transcript_text": "",
            "language": language or "en",
            "error": str(e),
            "isError": True,
            "error_type": "content",
            "action": "Transcripts may be disabled or unavailable for this video.",
        }

    except Exception as e:
        record_youtube_transcript(
            format=format,
            language=language or "en",
            is_translated=bool(translate_to),
            duration_seconds=None,
            backend_used=effective_backend,
        )
        LOGGER.warning("YouTube transcript unexpected error: %s", e)
        structured = format_tool_error(e, provider="youtube")
        emit_tool_observability_event(
            LOGGER,
            "youtube_transcript",
            "error",
            tool_call_id=tool_call_id,
            video_id_or_url=video_id_or_url,
            backend=effective_backend,
            error_type=structured["error_type"],
            error_message=str(structured["error"]),
            duration_ms=(time.monotonic() - started) * 1000,
        )
        return {  # type: ignore[return-value]
            "video_id": "",
            "video_url": video_id_or_url,
            "transcript_text": "",
            "language": language or "en",
            "error": structured["error"],
            "isError": True,
            "error_type": structured["error_type"],
            "action": structured.get("action"),
        }


async def youtube_search(
    query: str,
    num_results: int = 5,
    ctx: Context = CurrentContext(),
) -> YouTubeSearchResultType:
    """Find YouTube videos by search query via SearXNG.

    When to use this tool:
    - Use this tool FIRST to search YouTube and discover video targets before calling youtube_transcript.
    - Returns titles, links, video IDs, and snippets.

    Args:
        query: Search term for YouTube.
        num_results: Number of results to return (1-20, default 5).
    """

    if num_results < 1:
        num_results = 5
    num_results = min(num_results, 20)

    start_time = time.time()
    tool_call_id = str(uuid4())
    emit_tool_observability_event(
        LOGGER,
        "youtube_search",
        "request",
        tool_call_id=tool_call_id,
        query=query,
        num_results=num_results,
    )

    try:
        await ctx.report_progress(progress=20, total=100, message="Searching YouTube...")
        results, search_backend = await search_youtube(query, num_results=num_results)
        duration_seconds = time.time() - start_time

        # Record YouTube search telemetry
        record_youtube_search(
            num_results=len(results),
            duration_seconds=duration_seconds,
            search_backend=search_backend,
        )

        response = YouTubeSearchResponse(
            query=query,
            results=results,
            total_results=len(results),
            search_backend=search_backend,
        ).model_dump(exclude_none=True)

        emit_tool_observability_event(
            LOGGER,
            "youtube_search",
            "response",
            tool_call_id=tool_call_id,
            query=query,
            results=results,
            output_count=len(results),
            provider=search_backend,
            duration_ms=(time.time() - start_time) * 1000,
        )

        await ctx.report_progress(progress=100, total=100, message="Done")
        return response  # type: ignore[return-value]

    except (YouTubeSearchError, YouTubeApiError) as e:
        duration_seconds = time.time() - start_time
        record_youtube_search(
            num_results=0,
            duration_seconds=duration_seconds,
            search_backend="error",
        )
        emit_tool_observability_event(
            LOGGER,
            "youtube_search",
            "error",
            tool_call_id=tool_call_id,
            query=query,
            error_type=type(e).__name__,
            error_message=str(e),
            duration_ms=duration_seconds * 1000,
        )
        return {  # type: ignore[return-value]
            "query": query,
            "results": [],
            "error": str(e),
            "isError": True,
            "error_type": "network",
            "action": "YouTube search via SearXNG failed. Check SEARXNG_BASE_URL configuration.",
        }

    except Exception as e:
        duration_seconds = time.time() - start_time
        record_youtube_search(
            num_results=0,
            duration_seconds=duration_seconds,
        )
        LOGGER.warning("YouTube search unexpected error: %s", e)
        emit_tool_observability_event(
            LOGGER,
            "youtube_search",
            "error",
            tool_call_id=tool_call_id,
            query=query,
            error_type=type(e).__name__,
            error_message=str(e),
            duration_ms=duration_seconds * 1000,
        )
        return format_tool_error(e, provider="youtube")  # type: ignore[return-value]
