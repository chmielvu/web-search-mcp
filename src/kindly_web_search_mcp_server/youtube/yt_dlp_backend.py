"""yt-dlp subtitle extraction backend.

Uses yt-dlp's InnerTube API client rotation (7 clients) to extract
subtitles without downloading video. Works on cloud IPs that block
youtube-transcript-api.

Output format: [{"text": str, "start": float, "duration": float}, ...]
"""

from __future__ import annotations

import logging
from typing import Any

from .models import YouTubeError

logger = logging.getLogger(__name__)

# InnerTube API clients with different rate-limit buckets.
_YTDLP_CLIENTS = [
    "web",  # Web client — most reliable
    "android",  # Android app
    "ios",  # iOS app
    "tv",  # TV client
    "mweb",  # Mobile web
    "web_embed",  # Embedded web player
    "tv_embed",  # Embedded TV player
]


def ytdlp_extract_subtitles(
    video_id: str,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Extract subtitles via yt-dlp player API with client rotation.

    Returns: [{text, start, duration}, ...]
    Raises: YouTubeError if all clients fail.
    """
    try:
        import yt_dlp
    except ImportError:
        raise YouTubeError("yt-dlp not installed. Install with: pip install yt-dlp")

    url = f"https://www.youtube.com/watch?v={video_id}"
    target_lang = language or "en"
    last_error: Exception | None = None

    for client in _YTDLP_CLIENTS:
        try:
            segments = _try_client(yt_dlp, url, video_id, target_lang, client)
            if segments:
                logger.debug(
                    "yt-dlp succeeded with client=%s for video=%s",
                    client,
                    video_id,
                )
                return segments
        except Exception as exc:
            last_error = exc
            logger.debug(
                "yt-dlp client=%s failed for video=%s: %s",
                client,
                video_id,
                exc,
            )
            continue

    raise YouTubeError(
        f"yt-dlp: all {len(_YTDLP_CLIENTS)} clients failed for {video_id}. Last error: {last_error}"
    )


def _try_client(
    yt_dlp_module: Any,
    url: str,
    video_id: str,
    target_lang: str,
    client: str,
) -> list[dict[str, Any]]:
    """Try extracting subtitles with a specific InnerTube client."""
    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [target_lang, "en"],
        "subtitlesformat": "json3",
        "extractor_args": {"youtube": {"player_client": [client]}},
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 10,
    }

    with yt_dlp_module.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        return []

    # Try manual subtitles first, then auto-generated
    subtitles = info.get("subtitles") or {}
    auto_captions = info.get("automatic_captions") or {}

    subtitle_data = subtitles.get(target_lang) or subtitles.get("en")
    if not subtitle_data:
        subtitle_data = auto_captions.get(target_lang) or auto_captions.get("en")

    if not subtitle_data:
        return []

    return _parse_json3(subtitle_data)


def _parse_json3(subtitle_data: Any) -> list[dict[str, Any]]:
    """Parse yt-dlp json3 subtitle data into common segment format.

    json3 format: {"events": [{tStartMs, dDurationMs, segs: [{utf8}]}]}
    """
    import json

    raw: str | None = None

    if isinstance(subtitle_data, list):
        for entry in subtitle_data:
            if isinstance(entry, dict) and entry.get("ext") == "json3":
                url = entry.get("url")
                if url:
                    import httpx

                    resp = httpx.get(url, timeout=15, follow_redirects=True)
                    resp.raise_for_status()
                    raw = resp.text
                    break
    elif isinstance(subtitle_data, str):
        raw = subtitle_data

    if not raw:
        return []

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

    events = data.get("events", [])
    segments: list[dict[str, Any]] = []

    for event in events:
        segs = event.get("segs", [])
        if not segs:
            continue

        text_parts = []
        for seg in segs:
            utf8 = seg.get("utf8", "")
            if utf8 and utf8.strip():
                text_parts.append(utf8)

        text = " ".join(p.strip() for p in text_parts if p.strip())
        if not text:
            continue

        start_ms = event.get("tStartMs", 0)
        duration_ms = event.get("dDurationMs", 0)

        segments.append(
            {
                "text": text,
                "start": start_ms / 1000.0,
                "duration": duration_ms / 1000.0,
            }
        )

    return segments


def ytdlp_extract_metadata(video_id: str) -> dict[str, Any]:
    """Extract video metadata using yt-dlp (best-effort).

    Uses extract_flat=True for fast metadata-only extraction.
    Returns dict with keys: title, description, channel, channel_url,
    duration_seconds, view_count, upload_date, tags.

    All fields are optional and may be None if unavailable.
    Raises YouTubeError if yt-dlp is not installed.
    """
    try:
        import yt_dlp
    except ImportError:
        raise YouTubeError("yt-dlp not installed. Install with: pip install yt-dlp")

    url = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts = {
        "skip_download": True,
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 10,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.debug("yt-dlp metadata extraction failed for %s: %s", video_id, exc)
        return {}

    if not info or not isinstance(info, dict):
        return {}

    return {
        "title": info.get("title"),
        "description": info.get("description"),
        "channel": info.get("channel") or info.get("uploader"),
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "duration_seconds": info.get("duration"),
        "view_count": info.get("view_count"),
        "upload_date": info.get("upload_date"),
        "tags": info.get("tags"),
    }
