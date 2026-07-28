"""Cloudflare Workers AI Whisper backend for YouTube transcription.

Uses ``@cf/openai/whisper-large-v3-turbo`` via Cloudflare Workers AI REST API
at ``$0.00051/min`` (~214 free minutes/day on free tier).

Output format (matching all backends):
    [{"text": str, "start": float, "duration": float}, ...]

Audio download via yt-dlp, then POST to Cloudflare's AI inference endpoint.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
from typing import Any, Literal

import httpx

from ..settings import settings
from .models import YouTubeError

logger = logging.getLogger(__name__)

CLOUDFLARE_WHISPER_MODEL = "@cf/openai/whisper-large-v3-turbo"

_VTT_TIME_RE = re.compile(r"(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{3})")


class CfWhisperError(YouTubeError):
    """Raised when Cloudflare Whisper transcription fails."""


def _parse_vtt_timestamp(ts: str) -> float:
    """Parse VTT timestamp (HH:MM:SS.mmm) to seconds."""
    m = _VTT_TIME_RE.match(ts.strip())
    if not m:
        return 0.0
    h = int(m.group("h"))
    mn = int(m.group("m"))
    s = int(m.group("s"))
    ms = int(m.group("ms"))
    return float(h * 3600 + mn * 60 + s + ms / 1000.0)


def _parse_cloudflare_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse Cloudflare Workers AI response into common segment format.

    Expected response shape:
    {
        "success": true,
        "result": {
            "segments": [
                {
                    "vtt": "00:00:00.000 --> 00:00:01.000\\nHello world",
                    "text": "Hello world",
                    "start": 0.0,
                    "end": 1.0,
                    "word_count": 2
                },
                ...
            ]
        }
    }
    """
    if not isinstance(data, dict):
        raise CfWhisperError(f"Cloudflare API returned non-dict: {type(data).__name__}")

    success = data.get("success", False)
    if not success:
        errors = data.get("errors", [])
        error_msg = "; ".join(str(e) for e in errors) if errors else "unknown error"
        raise CfWhisperError(f"Cloudflare API returned success=false: {error_msg}")

    result = data.get("result")
    if not isinstance(result, dict):
        raise CfWhisperError(f"Cloudflare API result is not a dict: {type(result).__name__}")

    segments_raw = result.get("segments")
    if not isinstance(segments_raw, list):
        raise CfWhisperError("Cloudflare API response missing segments list")

    segments: list[dict[str, Any]] = []
    for seg in segments_raw:
        if not isinstance(seg, dict):
            continue

        # Parse VTT for start/duration, fall back to numeric start/end fields
        vtt = seg.get("vtt", "")
        if isinstance(vtt, str) and vtt.strip():
            lines = vtt.strip().split("\n")
            if len(lines) >= 1 and "-->" in lines[0]:
                parts = lines[0].split(" --> ")
                if len(parts) == 2:
                    start = _parse_vtt_timestamp(parts[0])
                    end = _parse_vtt_timestamp(parts[1])
                    duration = end - start
                else:
                    start = float(seg.get("start", 0.0))
                    duration = float(seg.get("end", start + 1.0)) - start
            else:
                start = float(seg.get("start", 0.0))
                duration = float(seg.get("end", start + 1.0)) - start
        else:
            start = float(seg.get("start", 0.0))
            duration = float(seg.get("end", start + 1.0)) - start

        text = seg.get("text", "")
        if not isinstance(text, str):
            text = str(text) if text is not None else ""

        if text.strip():
            segments.append(
                {
                    "text": text.strip(),
                    "start": start,
                    "duration": max(duration, 0.0),
                }
            )

    return segments


def _download_audio(video_id: str, max_seconds: int = 600) -> bytes:
    """Download audio from a YouTube video using yt-dlp.

    Returns raw MP3 bytes. Raises CfWhisperError on failure.
    """
    try:
        import yt_dlp
    except ImportError:
        raise CfWhisperError("yt-dlp not installed. Install with: pip install yt-dlp")

    url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": tmp_path.replace(".mp3", ""),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                }
            ],
            "max_filesize": None,
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }

        # If max_seconds is set, limit download to that duration
        if max_seconds and max_seconds > 0:

            def _duration_filter(info, *, incomplete):
                duration = info.get("duration")
                if duration and duration > max_seconds:
                    return f"Video too long ({duration}s > {max_seconds}s)"
                return None

            ydl_opts["match_filter"] = _duration_filter

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Read the downloaded file
        mp3_path = tmp_path
        if not os.path.exists(mp3_path):
            # yt-dlp may have used a different extension
            base = tmp_path.replace(".mp3", "")
            for ext in [".mp3", ".m4a", ".webm", ".opus", ".ogg"]:
                candidate = base + ext
                if os.path.exists(candidate):
                    mp3_path = candidate
                    break

        with open(mp3_path, "rb") as f:
            audio_bytes = f.read()

        return audio_bytes

    except Exception as exc:
        raise CfWhisperError(
            f"Audio download failed for video {video_id}: {type(exc).__name__}: {exc}"
        )
    finally:
        # Cleanup temp files
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            base = tmp_path.replace(".mp3", "")
            for ext in [".mp3", ".m4a", ".webm", ".opus", ".ogg"]:
                candidate = base + ext
                if os.path.exists(candidate):
                    os.unlink(candidate)
        except OSError:
            pass


def _get_api_url(account_id: str) -> str:
    """Build the Cloudflare Workers AI inference URL."""
    base = settings.cf_whisper_api_base_url.rstrip("/")
    return f"{base}/accounts/{account_id}/ai/run/{CLOUDFLARE_WHISPER_MODEL}"


def _transcribe_sync(
    video_id: str,
    *,
    language: str | None = None,
    task: Literal["transcribe", "translate"] = "transcribe",
    vad_filter: bool = True,
    max_audio_seconds: int | None = None,
) -> list[dict[str, Any]]:
    """Synchronous transcription entry point (runs in cascade thread).

    Steps:
        1. Download audio via yt-dlp
        2. POST to Cloudflare Workers AI
        3. Parse segments from response
    """
    account_id = settings.cf_whisper_account_id.strip()
    api_token = settings.cf_whisper_api_token.strip()

    if not account_id or not api_token:
        raise CfWhisperError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be configured")

    if max_audio_seconds is None:
        max_audio_seconds = settings.cf_whisper_max_audio_seconds

    # Step 1: Download audio
    audio_bytes = _download_audio(video_id, max_seconds=max_audio_seconds)
    if not audio_bytes:
        raise CfWhisperError(f"Downloaded audio for {video_id} is empty")

    # Step 2: POST to Cloudflare
    api_url = _get_api_url(account_id)
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

    payload: dict[str, Any] = {"audio": audio_b64}
    if language:
        payload["source_lang"] = language
    if task == "translate" and not language:
        payload["source_lang"] = "en"
    payload["task"] = task
    if vad_filter:
        payload["vad_filter"] = "true"

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(api_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 429:
            logger.warning("Cloudflare Whisper rate limited (429) for video %s, skipping", video_id)
            raise CfWhisperError("Cloudflare Whisper rate limited (429)")
        raise CfWhisperError(f"Cloudflare API returned HTTP {status}: {exc.response.text[:200]}")
    except httpx.TimeoutException:
        raise CfWhisperError(f"Cloudflare API timed out for video {video_id} (300s timeout)")
    except Exception as exc:
        raise CfWhisperError(f"Cloudflare API request failed: {type(exc).__name__}: {exc}")

    # Step 3: Parse segments
    return _parse_cloudflare_response(data)


async def transcribe_async(
    video_id: str,
    *,
    language: str | None = None,
    task: Literal["transcribe", "translate"] = "transcribe",
    vad_filter: bool = True,
    max_audio_seconds: int | None = None,
) -> list[dict[str, Any]]:
    """Async wrapper for use outside the cascade thread."""
    import asyncio

    return await asyncio.to_thread(
        _transcribe_sync,
        video_id,
        language=language,
        task=task,
        vad_filter=vad_filter,
        max_audio_seconds=max_audio_seconds,
    )
