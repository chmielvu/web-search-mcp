from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..settings import settings


class YouTubeError(RuntimeError):
    """Custom error for YouTube parsing/transcript failures."""

    pass


@dataclass(frozen=True)
class YouTubeTarget:
    video_id: str
    canonical_url: str  # e.g. https://www.youtube.com/watch?v=VIDEO_ID


# URL parsing patterns for various YouTube URL formats
_YOUTUBE_WATCH_RE = re.compile(r"^/watch$")
_YOUTUBE_EMBED_RE = re.compile(r"^/embed/([^/?]+)$")
_YOUTUBE_SHORTS_RE = re.compile(r"^/shorts/([^/?]+)$")
_YOUTUBE_LIVE_RE = re.compile(r"^/live/([^/?]+)$")


def parse_youtube_url(url: str) -> YouTubeTarget:
    """
    Parse YouTube URL and extract video ID.

    Supported formats:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    - https://www.youtube.com/shorts/VIDEO_ID
    - https://www.youtube.com/live/VIDEO_ID
    - Also accepts bare VIDEO_ID (11 chars, alphanumeric + - and _)

    Raises:
        YouTubeError: If URL is not a valid YouTube video URL.
    """
    # First check if it's a bare video ID (11 chars, alphanumeric + underscore/dash)
    stripped = url.strip()
    if re.match(r"^[\w-]{11}$", stripped) and not stripped.startswith(("http", "www.")):
        video_id = stripped
        return YouTubeTarget(
            video_id=video_id,
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        )

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    # Check for valid YouTube hosts
    valid_hosts = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
    if host not in valid_hosts:
        raise YouTubeError(f"Not a YouTube URL: host={host}")

    video_id: str | None = None
    path = parsed.path or ""

    # youtu.be short URL format
    if host == "youtu.be":
        video_id = path.lstrip("/")
        if not video_id:
            raise YouTubeError("youtu.be URL missing video ID")

    # /watch?v=VIDEO_ID format
    elif _YOUTUBE_WATCH_RE.match(path):
        query_params = parse_qs(parsed.query)
        v = query_params.get("v", [None])[0]
        if isinstance(v, str) and v.strip():
            video_id = v.strip()

    # /embed/VIDEO_ID format
    elif _YOUTUBE_EMBED_RE.match(path):
        match = _YOUTUBE_EMBED_RE.match(path)
        if match:
            video_id = match.group(1)

    # /shorts/VIDEO_ID format
    elif _YOUTUBE_SHORTS_RE.match(path):
        match = _YOUTUBE_SHORTS_RE.match(path)
        if match:
            video_id = match.group(1)

    # /live/VIDEO_ID format
    elif _YOUTUBE_LIVE_RE.match(path):
        match = _YOUTUBE_LIVE_RE.match(path)
        if match:
            video_id = match.group(1)

    if not video_id:
        raise YouTubeError(f"Could not extract video ID from URL: {url}")

    # Validate video ID format (11 chars for standard IDs)
    video_id = video_id.strip()
    if not re.match(r"^[\w-]{11}$", video_id):
        # Some IDs may be shorter or longer; accept them but log warning
        if len(video_id) < 1:
            raise YouTubeError(f"Empty video ID extracted from URL: {url}")

    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    return YouTubeTarget(video_id=video_id, canonical_url=canonical_url)


def extract_video_id(url_or_id: str) -> str:
    """
    Extract video ID from a URL or bare ID string.
    Convenience function for the MCP tool.
    """
    target = parse_youtube_url(url_or_id)
    return target.video_id


def fetch_transcript_data(
    video_id: str,
    language: str | None = None,
    translate_to: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch transcript using youtube-transcript-api library.

    Args:
        video_id: YouTube video ID
        language: Preferred language code (e.g., "en", "es"). If None, uses default.
        translate_to: Target language for translation (e.g., "de", "fr").

    Returns:
        List of transcript segments: [{text, start, duration}, ...]

    Raises:
        YouTubeError: If transcript fetch fails (no captions, IP blocked, etc.)
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

    # Configure proxy if set (for cloud environments where IPs are blocked)
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
            # Fallback: try without proxy config (may fail on cloud IPs)
            api = YouTubeTranscriptApi()
    else:
        api = YouTubeTranscriptApi()

    try:
        # List available transcripts first to find the best one
        transcript_list = api.list(video_id)

        # Find transcript with requested language
        try:
            transcript = transcript_list.find_transcript(languages)
        except NoTranscriptFound:
            # Try to find any available transcript
            available = list(transcript_list)
            if not available:
                raise YouTubeError("No transcripts available for this video")
            # Use first available
            transcript = available[0]

        # Translate if requested
        if translate_to:
            try:
                transcript = transcript.translate(translate_to)
            except Exception as e:
                raise YouTubeError(f"Translation failed: {e}")

        # Fetch the transcript
        fetched = transcript.fetch()

        # Convert to list of dicts
        segments = []
        for snippet in fetched:
            segments.append(
                {
                    "text": snippet.text,
                    "start": snippet.start,
                    "duration": snippet.duration,
                }
            )

        return segments

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
                "Set KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL to use a proxy. "
                f"Original error: {error_msg}"
            )
        raise YouTubeError(f"Could not retrieve transcript: {error_msg}")
    except Exception as e:
        raise YouTubeError(f"Transcript fetch failed: {type(e).__name__}: {e}")


def format_transcript_text(segments: list[dict[str, Any]]) -> str:
    """
    Format transcript as plain text (concatenated).
    """
    texts = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if text:
            texts.append(text)
    return " ".join(texts)


def format_transcript_timestamped(segments: list[dict[str, Any]]) -> str:
    """
    Format transcript with timestamps [MM:SS].
    """
    lines = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        start = seg.get("start", 0.0)
        minutes = int(start // 60)
        seconds = int(start % 60)
        timestamp = f"[{minutes:02d}:{seconds:02d}]"
        lines.append(f"{timestamp} {text}")
    return "\n".join(lines)


def calculate_total_duration(segments: list[dict[str, Any]]) -> float:
    """
    Calculate total video duration from transcript segments.
    """
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
    """
    Render YouTube transcript to deterministic Markdown.
    """
    lines = ["# YouTube Video Transcript", ""]

    # Metadata header
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
