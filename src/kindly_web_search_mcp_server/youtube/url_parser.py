"""YouTube URL parsing and video ID extraction.

Supported formats:
- https://www.youtube.com/watch?v=VIDEO_ID
- https://youtu.be/VIDEO_ID
- https://www.youtube.com/embed/VIDEO_ID
- https://www.youtube.com/shorts/VIDEO_ID
- https://www.youtube.com/live/VIDEO_ID
- Bare VIDEO_ID (11 chars, alphanumeric + - and _)
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .models import YouTubeError, YouTubeTarget

# URL path patterns
_YOUTUBE_WATCH_RE = re.compile(r"^/watch$")
_YOUTUBE_EMBED_RE = re.compile(r"^/embed/([^/?]+)$")
_YOUTUBE_SHORTS_RE = re.compile(r"^/shorts/([^/?]+)$")
_YOUTUBE_LIVE_RE = re.compile(r"^/live/([^/?]+)$")

_VALID_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")


_BARE_CHANNEL_ID_RE = re.compile(r"^UC[\w-]{18,30}$")
_HANDLE_RE = re.compile(r"^@[\w.-]+$")


def looks_like_channel_target(target: str) -> bool:
    """Return True when ``target`` is a channel handle, ID, or channel URL."""
    stripped = target.strip()
    if not stripped:
        return False
    if _BARE_CHANNEL_ID_RE.match(stripped) or _HANDLE_RE.match(stripped):
        return True
    if not stripped.startswith(("http://", "https://")):
        if not stripped.startswith(("youtube.com/", "www.youtube.com/", "m.youtube.com/", "youtu.be/")):
            return False
        stripped = f"https://{stripped}"
    parsed = urlparse(stripped)
    host = (parsed.hostname or "").lower()
    if host not in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        return False
    path = parsed.path or ""
    return path.startswith("/@") or "/channel/" in path


def parse_youtube_url(url: str) -> YouTubeTarget:
    """Parse YouTube URL and extract video ID.

    Raises:
        YouTubeError: If URL is not a valid YouTube video URL.
    """
    # Check if it's a bare video ID (11 chars, alphanumeric + underscore/dash)
    stripped = url.strip()
    if re.match(r"^[\w-]{11}$", stripped) and not stripped.startswith(("http", "www.")):
        video_id = stripped
        return YouTubeTarget(
            video_id=video_id,
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        )
    if not stripped.startswith(("http", "www.")) and re.fullmatch(r"[\w-]{5,60}", stripped):
        if looks_like_channel_target(stripped):
            raise YouTubeError(
                f"Expected a YouTube video ID or URL, got a channel target: {stripped!r}"
            )
        raise YouTubeError(
            f"Invalid YouTube video ID: {stripped!r} - expected 11 characters, got "
            f"{len(stripped)}."
        )

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host not in _VALID_HOSTS:
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

    # Validate video ID format
    video_id = video_id.strip()
    if not re.match(r"^[\w-]{11}$", video_id):
        raise YouTubeError(f"Invalid video ID extracted from URL: {url}")
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    return YouTubeTarget(video_id=video_id, canonical_url=canonical_url)


def extract_video_id(url_or_id: str) -> str:
    """Extract video ID from a URL or bare ID string."""
    target = parse_youtube_url(url_or_id)
    return target.video_id
