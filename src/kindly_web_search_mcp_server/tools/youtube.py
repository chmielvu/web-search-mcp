from __future__ import annotations

import asyncio
import logging
import time

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

LOGGER = logging.getLogger(__name__)


async def youtube_transcript(
    video_id_or_url: str,
    language: str | None = None,
    translate_to: str | None = None,
    format: str = "text",
    backend: str | None = None,
) -> YouTubeTranscriptResultType:
    """Extract captions from a YouTube video. Supports multiple URL formats, language selection, translation, and timestamped/text/JSON output.
    Use after youtube_search to get video content.
    backend: "auto" (cascade), "ytdlp" (yt-dlp only), "api" (legacy youtube-transcript-api). Default: server setting.
    """
    from ..settings import settings

    timeout_seconds = settings.youtube_transcript_timeout_seconds
    max_chars = settings.youtube_transcript_max_chars
    effective_backend = backend or settings.youtube_transcript_backend

    try:
        # Parse URL/ID
        target = parse_youtube_url(video_id_or_url)
        video_id = target.video_id
        canonical_url = target.canonical_url

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

        return YouTubeTranscriptResponse(
            video_id=video_id,
            video_url=canonical_url,
            title=None,  # Title requires separate YouTube Data API call (Phase 2)
            transcript_text=transcript_text,
            language=actual_language,
            is_translated=is_translated,
            duration_seconds=duration_seconds,
            transcript_segments=segments if format == "json" else None,
            error=None,
        ).model_dump(exclude_none=True)  # type: ignore[return-value]

    except asyncio.TimeoutError:
        record_youtube_transcript(
            format=format,
            language=language or "en",
            is_translated=bool(translate_to),
            duration_seconds=None,
            backend_used=effective_backend,
        )
        error_msg = f"Transcript fetch timed out after {timeout_seconds}s"
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
) -> YouTubeSearchResultType:
    """Find YouTube videos by search query. Returns titles, links, and snippets. Use before youtube_transcript."""

    if num_results < 1:
        num_results = 5
    num_results = min(num_results, 20)

    start_time = time.time()

    try:
        results, search_backend = await search_youtube(query, num_results=num_results)
        duration_seconds = time.time() - start_time

        # Record YouTube search telemetry
        record_youtube_search(
            num_results=len(results),
            duration_seconds=duration_seconds,
            search_backend=search_backend,
        )

        return YouTubeSearchResponse(
            query=query,
            results=results,
            total_results=len(results),
            search_backend=search_backend,
        ).model_dump(exclude_none=True)  # type: ignore[return-value]

    except (YouTubeSearchError, YouTubeApiError) as e:
        duration_seconds = time.time() - start_time
        record_youtube_search(
            num_results=0,
            duration_seconds=duration_seconds,
            search_backend="error",
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
        return format_tool_error(e, provider="youtube")  # type: ignore[return-value]
