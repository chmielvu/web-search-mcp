"""YouTube models: errors and data types."""

from __future__ import annotations

from dataclasses import dataclass


class YouTubeError(RuntimeError):
    """Custom error for YouTube parsing/transcript failures."""

    pass


class TranscriptBackendError(RuntimeError):
    """All transcript backends failed."""

    pass


class YouTubeSearchError(RuntimeError):
    """Custom error for YouTube search failures."""

    pass


class YouTubeApiError(RuntimeError):
    """Custom error for YouTube Data API v3 failures."""

    pass


@dataclass(frozen=True)
class YouTubeTarget:
    video_id: str
    canonical_url: str  # e.g. https://www.youtube.com/watch?v=VIDEO_ID
