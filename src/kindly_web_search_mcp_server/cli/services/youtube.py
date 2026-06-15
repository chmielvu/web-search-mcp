from __future__ import annotations

import asyncio
from typing import Any

from ...youtube import (
    calculate_total_duration,
    extract_video_id,
    fetch_transcript_cascade,
    format_transcript_text,
    format_transcript_timestamped,
    parse_youtube_url,
    search_youtube,
)
from ...settings import settings


async def fetch_youtube_search_payload(
    query: str,
    *,
    num_results: int,
) -> dict[str, Any]:
    results, search_backend = await search_youtube(query, num_results=num_results)
    return {
        "query": query,
        "results": [result.model_dump(exclude_none=True) for result in results],
        "total_results": len(results),
        "search_backend": search_backend,
    }


async def fetch_youtube_transcript_payload(
    video_id_or_url: str,
    *,
    language: str | None,
    translate_to: str | None,
    format: str,
    backend: str | None = None,
) -> dict[str, Any]:
    timeout_seconds = settings.youtube_transcript_timeout_seconds
    max_chars = settings.youtube_transcript_max_chars
    effective_backend = backend or settings.youtube_transcript_backend

    target = parse_youtube_url(video_id_or_url)
    video_id = extract_video_id(video_id_or_url)

    segments, backend_used = await asyncio.wait_for(
        asyncio.to_thread(
            fetch_transcript_cascade,
            video_id,
            language=language,
            translate_to=translate_to,
            backend=effective_backend,
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
        "backend_used": backend_used,
        "error": None,
    }
