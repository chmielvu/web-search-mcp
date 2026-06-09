from __future__ import annotations

import asyncio
from typing import Any

from ...content.youtube import (
    calculate_total_duration,
    extract_video_id,
    fetch_transcript_data,
    format_transcript_text,
    format_transcript_timestamped,
    parse_youtube_url,
)
from ...search.youtube import search_youtube_videos
from ...settings import settings


async def fetch_youtube_search_payload(
    query: str,
    *,
    num_results: int,
) -> dict[str, Any]:
    results = await search_youtube_videos(query, num_results=num_results)
    return {
        "query": query,
        "results": [result.model_dump(exclude_none=True) for result in results],
        "total_results": len(results),
    }


async def fetch_youtube_transcript_payload(
    video_id_or_url: str,
    *,
    language: str | None,
    translate_to: str | None,
    format: str,
) -> dict[str, Any]:
    timeout_seconds = settings.youtube_transcript_timeout_seconds
    max_chars = settings.youtube_transcript_max_chars

    target = parse_youtube_url(video_id_or_url)
    video_id = extract_video_id(video_id_or_url)

    segments = await asyncio.wait_for(
        asyncio.to_thread(
            fetch_transcript_data,
            video_id,
            language=language,
            translate_to=translate_to,
        ),
        timeout=timeout_seconds,
    )

    if format == "json":
        transcript_text = ""
    elif format == "timestamped":
        transcript_text = format_transcript_timestamped(segments)
    else:
        transcript_text = format_transcript_text(segments)

    if len(transcript_text) > max_chars:
        transcript_text = transcript_text[:max_chars].rstrip() + "…"

    return {
        "video_id": video_id,
        "video_url": target.canonical_url,
        "title": None,
        "transcript_text": transcript_text,
        "language": language or "en",
        "is_translated": bool(translate_to),
        "duration_seconds": calculate_total_duration(segments),
        "transcript_segments": segments if format == "json" else None,
        "error": None,
    }
