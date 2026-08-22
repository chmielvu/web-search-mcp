"""YouTube content resolver for Tier 1 content pipeline.

When fetch encounters a YouTube URL, this resolver produces rich
markdown containing: video title, channel, publish date, duration,
description, and transcript (from cascade).
"""

from __future__ import annotations

import logging
from typing import Any

from ...youtube.url_parser import parse_youtube_url, YouTubeTarget
from ...youtube.cascade import fetch_transcript_cascade
from ...youtube.yt_dlp_backend import ytdlp_extract_metadata
from ...youtube.models import YouTubeError, TranscriptBackendError

logger = logging.getLogger(__name__)


class YoutubeResolverError(RuntimeError):
    """Raised when YouTube content resolution fails completely."""


def _format_metadata_markdown(metadata: dict[str, Any]) -> str:
    """Format video metadata as markdown header."""
    lines: list[str] = []

    title = metadata.get("title")
    if title:
        lines.append(f"# {title}")

    channel = metadata.get("channel")
    channel_url = metadata.get("channel_url")
    if channel:
        if channel_url:
            lines.append(f"**Channel:** [{channel}]({channel_url})")
        else:
            lines.append(f"**Channel:** {channel}")

    duration = metadata.get("duration_seconds")
    if duration:
        minutes = int(duration) // 60
        seconds = int(duration) % 60
        if minutes >= 60:
            hours = minutes // 60
            minutes = minutes % 60
            lines.append(f"**Duration:** {hours}h {minutes}m {seconds}s")
        else:
            lines.append(f"**Duration:** {minutes}m {seconds}s")

    view_count = metadata.get("view_count")
    if view_count is not None:
        lines.append(f"**Views:** {view_count:,}")

    upload_date = metadata.get("upload_date")
    if upload_date:
        # Format: YYYYMMDD -> YYYY-MM-DD
        d = str(upload_date)
        if len(d) == 8:
            formatted = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            lines.append(f"**Published:** {formatted}")

    description = metadata.get("description")
    if description:
        lines.append("")
        lines.append("## Description")
        lines.append(description.strip()[:2000])  # Truncate long descriptions

    return "\n".join(lines) + "\n" if lines else ""


def _render_youtube_markdown(
    video_id: str,
    metadata: dict[str, Any],
    transcript_segments: list[dict[str, Any]] | None,
) -> str:
    """Render complete YouTube markdown with metadata + transcript."""
    lines: list[str] = []

    # Metadata header
    metadata_md = _format_metadata_markdown(metadata)
    if metadata_md:
        lines.append(metadata_md)

    # Transcript
    if transcript_segments:
        lines.append("")
        lines.append("## Transcript")
        lines.append("")
        for seg in transcript_segments:
            text = seg.get("text", "").strip()
            if text:
                start = seg.get("start", 0.0)
                minutes = int(start) // 60
                seconds = int(start) % 60
                ts = f"[{minutes:02d}:{seconds:02d}]"
                lines.append(f"{ts} {text}")
    else:
        lines.append("")
        lines.append("_Transcript unavailable for this video._")

    return "\n".join(lines).strip() + "\n"


async def fetch_youtube_content_markdown(
    url: str,
    *,
    http_client: Any = None,  # Unused, kept for API compatibility
) -> str:
    """Fetch YouTube video content as rich markdown.

    Args:
        url: YouTube video URL (all formats supported by parse_youtube_url).
        http_client: Unused, kept for API compatibility.

    Returns:
        Markdown string with video metadata and transcript.

    Raises:
        YoutubeResolverError: If resolution fails completely.
    """
    # Parse URL
    try:
        target = parse_youtube_url(url)
    except YouTubeError as exc:
        raise YoutubeResolverError(str(exc))

    video_id = target.video_id

    # Extract metadata (best-effort)
    metadata = ytdlp_extract_metadata(video_id)

    # Fetch transcript (best-effort)
    transcript_segments: list[dict[str, Any]] | None = None
    try:
        segments, _backend = fetch_transcript_cascade(
            video_id,
            backend="auto",
        )
        if segments:
            transcript_segments = segments
    except (YouTubeError, TranscriptBackendError):
        logger.debug("Transcript unavailable for video %s", video_id)

    # If both failed, raise
    if not metadata and not transcript_segments:
        raise YoutubeResolverError(f"Could not fetch any content for YouTube video {video_id}")

    return _render_youtube_markdown(video_id, metadata, transcript_segments)


def parse_youtube_content_url(url: str) -> YouTubeTarget | None:
    """Parse YouTube URL for content resolver. Returns None if not a YouTube URL."""
    try:
        return parse_youtube_url(url)
    except YouTubeError:
        return None
