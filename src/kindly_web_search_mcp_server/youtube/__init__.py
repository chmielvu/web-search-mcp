"""YouTube integration package.

Provides:
- URL parsing and video ID extraction
- Transcript fetching with cascade backends (yt-dlp → Cloudflare Whisper → HF Whisper → legacy youtube-transcript-api)
- Transcript formatting (plain text, timestamped, markdown)
- Video discovery lives in quick_web_search mode='youtube'
  (YouTube Data API → SearXNG → HTML scrape cascade in quick_web_search_youtube.py)
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
from .channel_api import list_channel_videos
from .api_quota import get_youtube_api_quota_tracker

__all__ = [
    # Models
    "YouTubeError",
    "YouTubeTarget",
    "TranscriptBackendError",
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
]
