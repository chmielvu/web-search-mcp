"""YouTube integration package.

Provides:
- URL parsing and video ID extraction
- Transcript fetching with cascade backends (yt-dlp → Cloudflare Whisper → HF Whisper → legacy youtube-transcript-api)
- Transcript formatting (plain text, timestamped, markdown)
- Video discovery lives in quick_web_search mode='youtube'
  (YouTube Data API → SearXNG → HTML scrape cascade in quick_web_search_youtube.py)
"""

from .api_quota import get_youtube_api_quota_tracker
from .cascade import (
    fetch_transcript_cascade,
    fetch_transcript_with_cache,
)
from .channel_api import list_channel_videos
from .models import TranscriptBackendError, YouTubeApiError, YouTubeError, YouTubeTarget
from .transcript import (
    calculate_total_duration,
    fetch_transcript_data,
    format_transcript_text,
    format_transcript_timestamped,
    render_youtube_transcript_markdown,
)
from .url_parser import extract_video_id, looks_like_channel_target, parse_youtube_url
from .whisper import CfWhisperError, WhisperClientError, fetch_hf_space_transcript_sync

__all__ = [
    "CfWhisperError",
    "TranscriptBackendError",
    # Whisper
    "WhisperClientError",
    "YouTubeApiError",
    # Models
    "YouTubeError",
    "YouTubeTarget",
    "calculate_total_duration",
    "extract_video_id",
    "fetch_hf_space_transcript_sync",
    "fetch_transcript_cascade",
    # Transcript
    "fetch_transcript_data",
    "fetch_transcript_with_cache",
    "format_transcript_text",
    "format_transcript_timestamped",
    "get_youtube_api_quota_tracker",
    "list_channel_videos",
    "looks_like_channel_target",
    # URL parsing
    "parse_youtube_url",
    "render_youtube_transcript_markdown",
]
