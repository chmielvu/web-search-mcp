"""YouTube transcript: legacy backend + formatting helpers.

The cascade orchestrator lives in cascade.py. This module provides:
- fetch_transcript_data(): legacy youtube-transcript-api backend
- Formatting: plain text, timestamped, markdown rendering
- Duration calculation
"""

from __future__ import annotations

import logging
from typing import Any

from ..settings import settings
from .models import YouTubeError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy youtube-transcript-api backend
# ---------------------------------------------------------------------------

def fetch_transcript_data(
    video_id: str,
    language: str | None = None,
    translate_to: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch transcript using youtube-transcript-api library.

    Args:
        video_id: YouTube video ID
        language: Preferred language code (e.g., "en", "es").
        translate_to: Target language for translation (e.g., "de", "fr").

    Returns:
        List of transcript segments: [{text, start, duration}, ...]

    Raises:
        YouTubeError: If transcript fetch fails.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            TranscriptsDisabled,
            CouldNotRetrieveTranscript,
            VideoUnavailable,
            NoTranscriptFound,
        )
    except ImportError:
        raise YouTubeError(
            "youtube-transcript-api not installed. "
            "Install with: pip install youtube-transcript-api"
        )

    languages = [language] if language else ["en"]

    proxy_url = settings.youtube_transcript_proxy_url
    if proxy_url:
        try:
            from youtube_transcript_api.proxies import GenericProxyConfig

            api = YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(
                    http_url=proxy_url,
                    https_url=proxy_url,
                )
            )
        except Exception:
            api = YouTubeTranscriptApi()
    else:
        api = YouTubeTranscriptApi()

    try:
        transcript_list = api.list(video_id)

        try:
            transcript = transcript_list.find_transcript(languages)
        except NoTranscriptFound:
            available = list(transcript_list)
            if not available:
                raise YouTubeError("No transcripts available for this video")
            transcript = available[0]

        if translate_to:
            try:
                transcript = transcript.translate(translate_to)
            except Exception as e:
                raise YouTubeError(f"Translation failed: {e}")

        fetched = transcript.fetch()
        return [
            {"text": s.text, "start": s.start, "duration": s.duration}
            for s in fetched
        ]

    except TranscriptsDisabled:
        raise YouTubeError("Transcripts are disabled for this video")
    except NoTranscriptFound:
        raise YouTubeError(f"No transcript found for language(s): {languages}")
    except VideoUnavailable:
        raise YouTubeError(
            "Video is unavailable (may be private, deleted, or age-restricted)"
        )
    except CouldNotRetrieveTranscript as e:
        error_msg = str(e)
        if "RequestBlocked" in error_msg or "IpBlocked" in error_msg:
            raise YouTubeError(
                "IP blocked by YouTube (common on AWS/GCP/Azure). "
                "Set YOUTUBE_TRANSCRIPT_PROXY_URL to use a proxy. "
                f"Original error: {error_msg}"
            )
        raise YouTubeError(f"Could not retrieve transcript: {error_msg}")
    except Exception as e:
        raise YouTubeError(f"Transcript fetch failed: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_transcript_text(segments: list[dict[str, Any]]) -> str:
    """Format transcript as plain text (concatenated)."""
    return " ".join(
        seg.get("text", "").strip()
        for seg in segments
        if seg.get("text", "").strip()
    )


def format_transcript_timestamped(segments: list[dict[str, Any]]) -> str:
    """Format transcript with timestamps [MM:SS]."""
    lines = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        start = seg.get("start", 0.0)
        minutes = int(start // 60)
        seconds = int(start % 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {text}")
    return "\n".join(lines)


def calculate_total_duration(segments: list[dict[str, Any]]) -> float:
    """Calculate total video duration from transcript segments."""
    if not segments:
        return 0.0
    last_seg = segments[-1]
    return last_seg.get("start", 0.0) + last_seg.get("duration", 0.0)


def render_youtube_transcript_markdown(
    *,
    video_id: str,
    title: str | None,
    transcript_text: str,
    language: str,
    is_translated: bool,
    source_url: str,
    duration_seconds: float | None,
) -> str:
    """Render YouTube transcript to deterministic Markdown."""
    lines = ["# YouTube Video Transcript", ""]

    title_str = title or f"Video {video_id}"
    lines.append(f"Video: {title_str}")
    lines.append(f"URL: {source_url}")
    lines.append(f"Language: {language}")
    if is_translated:
        lines.append("(Translated)")
    if duration_seconds:
        mins = int(duration_seconds // 60)
        secs = int(duration_seconds % 60)
        lines.append(f"Duration: {mins}:{secs:02d}")

    lines.append("")
    lines.append("## Transcript")
    lines.append("")
    lines.append(transcript_text)
    lines.append("")

    return "\n".join(lines).strip() + "\n"
