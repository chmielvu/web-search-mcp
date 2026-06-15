"""Backward-compat shim — YouTube search logic moved to youtube/ package.

Import from kindly_web_search_mcp_server.youtube instead.
This module will be removed in a future version.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "Importing from kindly_web_search_mcp_server.search.youtube is deprecated. "
    "Use kindly_web_search_mcp_server.youtube instead.",
    DeprecationWarning,
    stacklevel=2,
)

from ..youtube.models import YouTubeSearchError
from ..youtube.search import search_youtube_videos

__all__ = [
    "YouTubeSearchError",
    "search_youtube_videos",
]
