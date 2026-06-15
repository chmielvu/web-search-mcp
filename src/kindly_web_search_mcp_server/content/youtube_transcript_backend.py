"""Backward-compat shim — cascade logic moved to youtube/cascade.py.

Import fetch_transcript_cascade from kindly_web_search_mcp_server.youtube instead.
This module will be removed in a future version.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "Importing from kindly_web_search_mcp_server.content.youtube_transcript_backend is deprecated. "
    "Use kindly_web_search_mcp_server.youtube instead.",
    DeprecationWarning,
    stacklevel=2,
)

from ..youtube.cascade import fetch_transcript_cascade
from ..youtube.models import TranscriptBackendError

__all__ = [
    "fetch_transcript_cascade",
    "TranscriptBackendError",
]
