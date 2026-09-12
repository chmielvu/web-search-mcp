"""YouTube integration package.

Provides:
- URL parsing and video ID extraction
- Transcript fetching with cascade backends (yt-dlp → Cloudflare Whisper → HF Whisper → legacy youtube-transcript-api)
- Transcript formatting (plain text, timestamped, markdown)
- YouTube search with API → SearXNG → HTML scrape fallback
- Video metadata enrichment via YouTube Data API v3
"""

from .models import YouTubeError, YouTubeTarget, TranscriptBackendError, YouTubeApiError
from .url_parser import parse_youtube_url, extract_video_id, looks_like_channel_target
from .transcript import (
    fetch_transcript_data,
    format_transcript_text,
    format_transcript_timestamped,
    calculate_total_duration,
    render_youtube_transcript_markdown,
)
from .cascade import (
    fetch_transcript_cascade,
    fetch_transcript_with_cache,
)
from .whisper import WhisperClientError, CfWhisperError, fetch_hf_space_transcript_sync
from .search import (
    search_youtube,
    search_youtube_videos,
    search_youtube_html_scrape,
    YouTubeSearchError,
)
from .api_enrichment import enrich_video_metadata, _parse_iso8601_duration
from .channel_api import list_channel_videos
from .api_quota import get_youtube_api_quota_tracker

__all__ = [
    # Models
    "YouTubeError",
    "YouTubeTarget",
    "TranscriptBackendError",
    "YouTubeSearchError",
    "YouTubeApiError",
    # URL parsing
    "parse_youtube_url",
    "extract_video_id",
    "looks_like_channel_target",
    # Transcript
    "fetch_transcript_data",
    "fetch_transcript_cascade",
    "fetch_transcript_with_cache",
    "format_transcript_text",
    "format_transcript_timestamped",
    "calculate_total_duration",
    "render_youtube_transcript_markdown",
    # Whisper
    "WhisperClientError",
    "CfWhisperError",
    "fetch_hf_space_transcript_sync",
    "list_channel_videos",
    "get_youtube_api_quota_tracker",
    # Search
    "search_youtube",
    "search_youtube_videos",
    "search_youtube_html_scrape",
    # Enrichment
    "enrich_video_metadata",
    "_parse_iso8601_duration",
]
