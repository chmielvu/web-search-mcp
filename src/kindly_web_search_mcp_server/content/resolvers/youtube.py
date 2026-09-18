"""YouTube content resolver for Tier 1 content pipeline.

When fetch encounters a YouTube URL, this resolver produces rich
markdown containing the video transcript (from the scraper cascade).
"""

from __future__ import annotations

import logging
from typing import Any

from ...utils.youtube_urls import YouTubeError, parse_youtube_url
from ..models import FetchContext, ParsedURL, RawDocument, ResolverTarget
from ..youtube_transcripts import (
    ScraperTranscriptError,
    fetch_transcript_cascade,
    format_transcript_timestamped,
)
from ._bridge import bridge_text_producer

logger = logging.getLogger(__name__)


class YoutubeResolverError(RuntimeError):
    """Raised when YouTube content resolution fails completely."""


def _render_youtube_markdown(
    video_id: str,
    transcript_segments: list[dict[str, Any]],
) -> str:
    """Render YouTube markdown with transcript."""
    lines: list[str] = [
        f"# YouTube {video_id}",
        "",
        f"https://www.youtube.com/watch?v={video_id}",
        "",
        "## Transcript",
        "",
        format_transcript_timestamped(transcript_segments),
    ]
    return "\n".join(lines).strip() + "\n"


async def fetch_youtube_content_raw(
    url: str,
    *,
    http_client: Any = None,  # Unused, kept for API compatibility
) -> dict[str, object]:
    """Fetch YouTube transcript pieces without final rendering.

    Args:
        url: YouTube video URL (all formats supported by parse_youtube_url).
        http_client: Unused, kept for API compatibility.

    Returns:
        Dict with title, rendered markdown body, completeness, and coverage.

    Raises:
        YoutubeResolverError: If resolution fails completely.
    """
    # Parse URL
    try:
        target = parse_youtube_url(url)
    except YouTubeError as exc:
        raise YoutubeResolverError(str(exc)) from exc

    video_id = target.video_id

    # Fetch transcript (best-effort)
    transcript_segments: list[dict[str, Any]] | None = None
    try:
        segments, _backend = await fetch_transcript_cascade(
            video_id,
            backend="auto",
        )
        if segments:
            transcript_segments = segments
    except (YouTubeError, ScraperTranscriptError):
        logger.debug("Transcript unavailable for video %s", video_id)

    # If transcript failed, raise
    if not transcript_segments:
        raise YoutubeResolverError(f"Could not fetch any content for YouTube video {video_id}")

    title = f"YouTube {video_id}"
    markdown = _render_youtube_markdown(video_id, transcript_segments)
    return {
        "title": title,
        "markdown": markdown,
        "complete": transcript_segments is not None,
        "coverage": {
            "transcript_available": transcript_segments is not None,
            "segment_count": len(transcript_segments) if transcript_segments else 0,
        },
    }


async def fetch_youtube_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire YouTube transcript cascade output."""
    return await bridge_text_producer(
        target,
        ctx,
        "youtube",
        fetch_youtube_content_raw,
    )


def match_youtube(parsed: ParsedURL) -> ResolverTarget | None:
    host = (parsed.parts.hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        return ResolverTarget(url=parsed.url, kind="youtube", values={"url": parsed.url})
    return None
