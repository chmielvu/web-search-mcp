"""Whisper VPS backend service client for YouTube transcription.

Posts YouTube video requests to the self-hosted VPS Whisper service
(e.g., hosted on Unified ML or dedicated VPS ASR service at WHISPER_VPS_URL)
and parses returned segment JSON into common transcript segment dicts:
  [{"text": str, "start": float, "duration": float}, ...]
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ..settings import settings
from .models import YouTubeError

logger = logging.getLogger(__name__)


class VpsWhisperError(YouTubeError):
    """Raised when the Whisper VPS service call fails."""


def _build_vps_endpoint(base_url: str) -> str:
    """Normalize the VPS Whisper service endpoint URL."""
    base = base_url.rstrip("/")
    if base.endswith("/transcribe") or base.endswith("/predict") or base.endswith("/asr"):
        return base
    return f"{base}/transcribe"


def _parse_vps_response(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse VPS Whisper service response into common segment list format."""
    if not isinstance(raw, dict):
        raise VpsWhisperError(f"VPS Whisper returned invalid response type: {type(raw).__name__}")

    # Handle standard JSON format: {"segments": [{"text": "...", "start": 0.0, "duration": 1.0}]}
    if "segments" in raw and isinstance(raw["segments"], list):
        segments: list[dict[str, Any]] = []
        for seg in raw["segments"]:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            start = float(seg.get("start", 0.0))
            duration = float(seg.get("duration", 0.0))
            segments.append({"text": text, "start": start, "duration": duration})
        if segments:
            return segments

    # Handle fallback transcript text format: {"text": "..."} or {"transcript": "..."}
    text_val = raw.get("text") or raw.get("transcript")
    if isinstance(text_val, str) and text_val.strip():
        return [{"text": text_val.strip(), "start": 0.0, "duration": 0.0}]

    # Handle Gradio-style wrapper: {"data": [...]}
    data = raw.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, str):
            try:
                parsed = json.loads(first)
                if isinstance(parsed, dict):
                    return _parse_vps_response(parsed)
            except (json.JSONDecodeError, TypeError):
                return [{"text": first.strip(), "start": 0.0, "duration": 0.0}]

    raise VpsWhisperError("VPS Whisper response contained no valid transcript segments")


def fetch_vps_whisper_transcript_sync(
    video_id: str,
    *,
    language: str | None = None,
    task: str = "transcribe",
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Synchronous transcription entry point using VPS Whisper service.

    Args:
        video_id: YouTube video ID.
        language: Optional language code.
        task: "transcribe" or "translate".
        timeout_seconds: Custom timeout in seconds.

    Returns:
        List of segment dicts: [{"text": str, "start": float, "duration": float}, ...]
    """
    vps_url = settings.whisper_vps_url.strip()
    if not vps_url:
        raise VpsWhisperError(
            "WHISPER_VPS_URL is not configured. Set WHISPER_VPS_URL to use the VPS Whisper service."
        )

    timeout = (
        timeout_seconds if timeout_seconds is not None else settings.whisper_vps_timeout_seconds
    )
    endpoint = _build_vps_endpoint(vps_url)
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"

    payload = {
        "video_id": video_id,
        "url": canonical_url,
        "language": language,
        "task": task,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            return _parse_vps_response(data)
    except httpx.TimeoutException as exc:
        raise VpsWhisperError(f"VPS Whisper request timed out after {timeout}s") from exc
    except httpx.HTTPStatusError as exc:
        raise VpsWhisperError(f"VPS Whisper returned HTTP {exc.response.status_code}") from exc
    except Exception as exc:
        raise VpsWhisperError(f"VPS Whisper request failed: {type(exc).__name__}: {exc}") from exc


async def fetch_vps_whisper_transcript(
    video_id: str,
    *,
    language: str | None = None,
    task: str = "transcribe",
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Async wrapper for VPS Whisper transcription."""
    vps_url = settings.whisper_vps_url.strip()
    if not vps_url:
        raise VpsWhisperError(
            "WHISPER_VPS_URL is not configured. Set WHISPER_VPS_URL to use the VPS Whisper service."
        )

    timeout = (
        timeout_seconds if timeout_seconds is not None else settings.whisper_vps_timeout_seconds
    )
    endpoint = _build_vps_endpoint(vps_url)
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"

    payload = {
        "video_id": video_id,
        "url": canonical_url,
        "language": language,
        "task": task,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            return _parse_vps_response(data)
    except httpx.TimeoutException as exc:
        raise VpsWhisperError(f"VPS Whisper request timed out after {timeout}s") from exc
    except httpx.HTTPStatusError as exc:
        raise VpsWhisperError(f"VPS Whisper returned HTTP {exc.response.status_code}") from exc
    except Exception as exc:
        raise VpsWhisperError(f"VPS Whisper request failed: {type(exc).__name__}: {exc}") from exc
