"""YouTube integration package.

Provides:
- URL parsing and video ID extraction
- Transcript fetching with cascade backends (yt-dlp → Cloudflare Whisper → HF Whisper → legacy youtube-transcript-api)
- Transcript formatting (plain text, timestamped, markdown)
- YouTube search with API → SearXNG → HTML scrape fallback
- Channel handle resolution
- Video metadata enrichment via YouTube Data API v3
"""

from .models import YouTubeError, YouTubeTarget, TranscriptBackendError, YouTubeApiError
from .url_parser import parse_youtube_url, extract_video_id
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
from .search import (
    search_youtube,
    search_youtube_videos,
    search_youtube_html_scrape,
    resolve_channel_handle,
    search_channel_videos,
    YouTubeSearchError,
)
from .whisper_client import (
    WhisperClientError,
    fetch_whisper_transcript,
    fetch_whisper_transcript_sync,
)
from .cf_whisper import (
    CfWhisperError,
    _transcribe_sync,
    transcribe_async,
)
from .api_enrichment import enrich_video_metadata, _parse_iso8601_duration

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
    "fetch_whisper_transcript",
    "fetch_whisper_transcript_sync",
    # Cloudflare Whisper
    "CfWhisperError",
    "_transcribe_sync",
    "transcribe_async",
    # Search
    "search_youtube",
    "search_youtube_videos",
    "search_youtube_html_scrape",
    "resolve_channel_handle",
    "search_channel_videos",
    # Enrichment
    "enrich_video_metadata",
    "_parse_iso8601_duration",
]
