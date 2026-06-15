"""HF Whisper Space client for YouTube transcription via Gradio API.

Posts a YouTube URL to a self-hosted Whisper HF Space and parses the
returned segment JSON.  Both async and sync entry points are provided
because the transcript cascade runs inside ``asyncio.to_thread()``.

Output format: [{"text": str, "start": float, "duration": float}, ...]
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..settings import settings

logger = logging.getLogger(__name__)


class WhisperClientError(RuntimeError):
    """Raised when the Whisper Space call fails."""


def _build_space_url(space_url: str) -> str:
    """Normalise the Gradio API endpoint."""
    base = space_url.rstrip("/")
    if not base.endswith("/api/predict"):
        base = f"{base}/api/predict"
    return base


def _parse_gradio_response(raw_json: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract transcript segments from a Gradio /api/predict response.

    Expected envelope: {"data": ["<json_string>"]}
    Inner JSON may contain {"segments": [...]} or be plain text.
    """
    data_field = raw_json.get("data")
    if not isinstance(data_field, list) or not data_field:
        raise WhisperClientError(
            "Whisper Space returned no 'data' array in response"
        )

    payload_str = data_field[0]
    if not isinstance(payload_str, str):
        raise WhisperClientError(
            "Whisper Space data[0] is not a string"
        )

    try:
        payload = json.loads(payload_str)
    except (json.JSONDecodeError, TypeError):
        # Plain text transcript fallback
        return [{"text": payload_str.strip(), "start": 0.0, "duration": 0.0}]

    if not isinstance(payload, dict):
        return [{"text": payload_str.strip(), "start": 0.0, "duration": 0.0}]

    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        # Valid JSON but no segments key — treat as plain text
        return [{"text": payload_str.strip(), "start": 0.0, "duration": 0.0}]

    segments: list[dict[str, Any]] = []
    for seg in raw_segments:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        segments.append({
            "text": text,
            "start": float(seg.get("start", 0.0)),
            "duration": float(seg.get("duration", 0.0)),
        })

    if not segments:
        return [{"text": payload_str.strip(), "start": 0.0, "duration": 0.0}]
    return segments


async def fetch_whisper_transcript(
    youtube_url: str,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """POST to a Whisper HF Space and return transcript segments.

    Args:
        youtube_url: Full YouTube video URL.
        timeout_seconds: HTTP timeout; defaults to WHISPER_SPACE_TIMEOUT_SECONDS
            from settings (300 s).

    Returns:
        List of segment dicts: ``[{"text", "start", "duration"}, ...]``

    Raises:
        WhisperClientError: Missing URL, timeout, HTTP error, bad response.
    """
    import httpx

    space_url = settings.whisper_space_url.strip()
    if not space_url:
        raise WhisperClientError(
            "WHISPER_SPACE_URL is not configured. "
            "Set it to a Whisper HF Space URL."
        )

    timeout = timeout_seconds if timeout_seconds is not None else settings.whisper_space_timeout_seconds
    api_url = _build_space_url(space_url)
    body = {"data": [youtube_url]}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(api_url, json=body)
            resp.raise_for_status()
            raw = resp.json()
    except httpx.TimeoutException as exc:
        raise WhisperClientError(
            f"Whisper Space request timed out after {timeout}s"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise WhisperClientError(
            f"Whisper Space returned HTTP {exc.response.status_code}"
        ) from exc
    except Exception as exc:
        raise WhisperClientError(
            f"Whisper Space request failed: {type(exc).__name__}: {exc}"
        ) from exc

    return _parse_gradio_response(raw)


def fetch_whisper_transcript_sync(
    youtube_url: str,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Synchronous wrapper for use in the transcript cascade (runs in a thread).

    Same contract as :func:`fetch_whisper_transcript` but uses ``httpx.Client``.
    """
    import httpx

    space_url = settings.whisper_space_url.strip()
    if not space_url:
        raise WhisperClientError(
            "WHISPER_SPACE_URL is not configured. "
            "Set it to a Whisper HF Space URL."
        )

    timeout = timeout_seconds if timeout_seconds is not None else settings.whisper_space_timeout_seconds
    api_url = _build_space_url(space_url)
    body = {"data": [youtube_url]}

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(api_url, json=body)
            resp.raise_for_status()
            raw = resp.json()
    except httpx.TimeoutException as exc:
        raise WhisperClientError(
            f"Whisper Space request timed out after {timeout}s"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise WhisperClientError(
            f"Whisper Space returned HTTP {exc.response.status_code}"
        ) from exc
    except Exception as exc:
        raise WhisperClientError(
            f"Whisper Space request failed: {type(exc).__name__}: {exc}"
        ) from exc

    return _parse_gradio_response(raw)
