"""Backward-compat shim — all YouTube content logic moved to youtube/ package.

Import from kindly_web_search_mcp_server.youtube instead.
This module will be removed in a future version.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "Importing from kindly_web_search_mcp_server.content.youtube is deprecated. "
    "Use kindly_web_search_mcp_server.youtube instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export everything from the new location
from ..youtube.models import YouTubeError, YouTubeTarget
from ..youtube.url_parser import parse_youtube_url, extract_video_id
from ..youtube.transcript import (
    fetch_transcript_data,
    format_transcript_text,
    format_transcript_timestamped,
    calculate_total_duration,
    render_youtube_transcript_markdown,
)

__all__ = [
    "YouTubeError",
    "YouTubeTarget",
    "parse_youtube_url",
    "extract_video_id",
    "fetch_transcript_data",
    "format_transcript_text",
    "format_transcript_timestamped",
    "calculate_total_duration",
    "render_youtube_transcript_markdown",
]
