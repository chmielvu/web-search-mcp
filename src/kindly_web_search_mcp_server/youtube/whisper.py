"""Whisper ASR transcript backends for videos without captions.

Audio sources, in order:
  1. Self-hosted cobalt instance (COBALT_BASE_URL): fetches audio on the
     VPS, where YouTube does not enforce the per-stream byte cap.
  2. yt-dlp locally (subject to YouTube bot-gates).
The bytes are then posted to Cloudflare Workers AI (whisper-large-v3-turbo).

Hugging Face Whisper Space posts the video URL to a Gradio /api/predict
endpoint.

Output format: [{"text": str, "start": float, "duration": float}, ...]
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
from typing import Any, Literal

import httpx

from ..settings import settings
from .models import YouTubeError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cloudflare Workers AI Whisper
# ---------------------------------------------------------------------------

CLOUDFLARE_WHISPER_MODEL = "@cf/openai/whisper-large-v3-turbo"

_VTT_TIME_RE = re.compile(r"(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{3})")


class CfWhisperError(YouTubeError):
    """Raised when Cloudflare Whisper transcription fails."""


def _parse_vtt_timestamp(ts: str) -> float:
    """Parse VTT timestamp (HH:MM:SS.mmm) to seconds."""
    match = _VTT_TIME_RE.match(ts.strip())
    if not match:
        return 0.0
    h = int(match.group("h"))
    mn = int(match.group("m"))
    s = int(match.group("s"))
    ms = int(match.group("ms"))
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
    """Download audio for ASR: self-hosted cobalt first, local yt-dlp fallback.

    Returns raw MP3 bytes. Raises CfWhisperError on failure.
    """
    try:
        import yt_dlp
    except ImportError:
        raise CfWhisperError("yt-dlp not installed. Install with: pip install yt-dlp")

    url = f"https://www.youtube.com/watch?v={video_id}"

    # Reserve the name, then delete: a 0-byte placeholder would otherwise be
    # "found" as the converted output and mask the real yt-dlp failure with
    # "Downloaded audio is empty".
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name
    os.unlink(tmp_path)

    if settings.cobalt_base_url.strip():
        try:
            return _download_audio_via_cobalt(video_id, tmp_path, max_seconds)
        except CobaltAudioError as exc:
            logger.warning("cobalt audio fetch failed for %s: %s; falling back to yt-dlp", video_id, exc)
        except Exception as exc:
            logger.warning("cobalt audio fetch unexpected error for %s: %s; falling back to yt-dlp", video_id, exc)

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
            "js_runtimes": {"node": {}},
        }

        # If max_seconds is set, limit download to that duration
        if max_seconds and max_seconds > 0:

            def _duration_filter(info, *, incomplete):
                duration = info.get("duration")
                if duration and duration > max_seconds:
                    return f"Video too long ({duration}s > {max_seconds}s)"
                return None

            ydl_opts["match_filter"] = _duration_filter

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # pyright: ignore[reportArgumentType]
            ydl.download([url])

        # Read the downloaded file; yt-dlp may have used a different extension
        base = tmp_path.replace(".mp3", "")
        mp3_path = next(
            (candidate for ext in [".mp3", ".m4a", ".webm", ".opus", ".ogg"]
             for candidate in [base + ext]
             if os.path.exists(candidate)),
            None,
        )
        if mp3_path is None:
            raise CfWhisperError(
                f"Audio download produced no file for video {video_id} (yt-dlp matched"
                " no output; check YouTube bot-block / format availability)."
            )
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


class CobaltAudioError(RuntimeError):
    """Raised when the self-hosted cobalt audio fetch fails."""


def _download_audio_via_cobalt(video_id: str, dest_path: str, max_seconds: int) -> bytes:
    """Fetch audio bytes through the self-hosted cobalt instance.

    cobalt streams the audio from its own egress (no local byte cap). The
    response body is the raw media file; it is written to dest_path and
    returned. Raises CobaltAudioError on any failure so the caller can
    fall back to local yt-dlp.
    """
    base_url = settings.cobalt_base_url.strip().rstrip("/")
    if not base_url:
        raise CobaltAudioError("COBALT_BASE_URL not configured")

    body = {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "downloadMode": "audio",
        "audioFormat": "mp3",
        "localProcessing": "disabled",
    }

    try:
        with httpx.Client(timeout=settings.cobalt_timeout_seconds) as client:
            resp = client.post(
                f"{base_url}/",
                json=body,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                detail = resp.text[:200]
                raise CobaltAudioError(f"cobalt POST returned HTTP {resp.status_code}: {detail}")
            data = resp.json()

            status = data.get("status")
            if status == "error":
                code = (data.get("error") or {}).get("code", "unknown")
                raise CobaltAudioError(f"cobalt error: {code}")

            if status == "tunnel" or status == "redirect":
                stream_url = data.get("url")
                if not stream_url:
                    raise CobaltAudioError(f"cobalt response missing url (status={status})")
                audio_resp = client.get(stream_url)
                audio_resp.raise_for_status()
            elif status == "local-processing":
                # Cobalt returns tunnel URLs for ffmpeg-side processing even in
                # local-processing mode; the audio tunnel is what we need.
                tunnels = data.get("tunnel") or []
                audio_tunnel = next(
                    (t for t in tunnels if str((t or {}).get("type", "")).lower() == "audio"),
                    None,
                )
                if audio_tunnel is None:
                    raise CobaltAudioError("cobalt local-processing response has no audio tunnel")
                audio_resp = client.get(audio_tunnel["urls"][0])
                audio_resp.raise_for_status()
            else:
                raise CobaltAudioError(f"unexpected cobalt status: {status}")

        audio_bytes = audio_resp.content
        if not audio_bytes:
            raise CobaltAudioError("cobalt returned empty audio body")
        if max_seconds and max_seconds > 0 and len(audio_bytes) > 50 * 1024 * 1024:
            # Crude guard: 50MB of mp3 at 128kbps is ~52 minutes. cobalt's
            # DURATION_LIMIT already gates duration server-side; this is a
            # belt-and-braces guard for ASR payload size.
            raise CobaltAudioError(f"cobalt audio too large: {len(audio_bytes)} bytes")

        with open(dest_path, "wb") as f:
            f.write(audio_bytes)
        return audio_bytes

    except CobaltAudioError:
        raise
    except httpx.TimeoutException as exc:
        raise CobaltAudioError(f"cobalt request timed out after {settings.cobalt_timeout_seconds}s") from exc
    except Exception as exc:
        raise CobaltAudioError(f"cobalt request failed: {type(exc).__name__}: {exc}") from exc


def _get_api_url(account_id: str) -> str:
    """Build the Cloudflare Workers AI inference URL."""
    base = settings.cf_whisper_api_base_url.rstrip("/")
    return f"{base}/accounts/{account_id}/ai/run/{CLOUDFLARE_WHISPER_MODEL}"


def fetch_cloudflare_transcript_sync(
    video_id: str,
    *,
    language: str | None = None,
    task: Literal["transcribe", "translate"] = "transcribe",
    vad_filter: bool = True,
    max_audio_seconds: int | None = None,
) -> list[dict[str, Any]]:
    """Transcribe a video via Cloudflare Workers AI Whisper.

    Steps:
        1. Download audio via yt-dlp
        2. POST to Cloudflare Workers AI
        3. Parse segments from response

    Called from the transcript cascade worker thread.
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


# ---------------------------------------------------------------------------
# Hugging Face Whisper Space (Gradio)
#
# Public ZeroGPU Space tier. Disabled unless WHISPER_SPACE_ID is set; the
# hunt on 2026-09-12 found URL-native Spaces (DopeyFace/whisper_youtube_transcribe,
# kazuk/youtube-whisper-08) whose yt-dlp downloads are YouTube bot-blocked,
# so treat any public Space as best-effort and keep Cloudflare as the
# reliable fallback. Configure WHISPER_SPACE_ID to a Space exposing a
# youtube-URL transcribe endpoint and gradio-client will be used when
# installed (pip install gradio_client).
# ---------------------------------------------------------------------------

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
        raise WhisperClientError("Whisper Space returned no 'data' array in response")

    payload_str = data_field[0]
    if not isinstance(payload_str, str):
        raise WhisperClientError("Whisper Space data[0] is not a string")

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
        segments.append(
            {
                "text": text,
                "start": float(seg.get("start", 0.0)),
                "duration": float(seg.get("duration", 0.0)),
            }
        )

    if not segments:
        return [{"text": payload_str.strip(), "start": 0.0, "duration": 0.0}]
    return segments


def _call_space_via_client(space_id: str, video_id: str, timeout: float) -> list[dict[str, Any]]:
    """Call a public Space through gradio_client (stub tier).

    Supports the two URL-native API shapes observed in the 2026-09-12 hunt:
      - /process_yt_transcribe(yt_url, task)  -> (text, info)
      - /get_transcript(url, model_size, lang, format) -> text
    Falls back to the first named endpoint whose parameters contain a URL
    string field. Returns segment dicts; raises WhisperClientError on any
    failure (cascade treats it as a normal backend miss).
    """
    try:
        from gradio_client import Client
    except ImportError as exc:
        raise WhisperClientError(
            "gradio_client is not installed; pip install gradio_client to use WHISPER_SPACE_ID"
        ) from exc

    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        client = Client(space_id, verbose=False)
        api = client.view_api(return_format="dict") or {}
        named = api.get("named_endpoints") or {}
        if not named:
            raise WhisperClientError(f"Space {space_id} exposes no named API endpoints")

        def _is_url_param(param: dict[str, Any]) -> bool:
            label = str(param.get("parameter_name") or param.get("label") or "")
            py_type = str(param.get("type") or "")
            return "url" in label.lower() and "str" in py_type

        for endpoint_name, spec in named.items():
            params = spec.get("parameters", [])
            url_params = [p for p in params if _is_url_param(p)]
            if not url_params:
                continue
            kwargs: dict[str, Any] = {}
            for p in params:
                pname = p.get("parameter_name")
                if pname is None:
                    continue
                if p in url_params:
                    kwargs[pname] = youtube_url
            result = client.predict(api_name=endpoint_name, **kwargs)
            return _result_to_segments(result)

        raise WhisperClientError(
            f"Space {space_id} has no URL-parameter endpoint among: {list(named)}"
        )
    except WhisperClientError:
        raise
    except Exception as exc:
        raise WhisperClientError(f"Space {space_id} call failed: {type(exc).__name__}: {exc}") from exc


def _result_to_segments(result: Any) -> list[dict[str, Any]]:
    """Convert a gradio_client predict result into cascade segment dicts."""
    if isinstance(result, (tuple, list)):
        payload = result[0] if result else ""
    else:
        payload = result
    text = str(payload or "").strip()
    if not text:
        raise WhisperClientError("Space returned an empty transcript")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "text" in parsed[0]:
        return [
            {
                "text": str(seg.get("text", "")).strip(),
                "start": float(seg.get("start", 0.0)),
                "duration": float(seg.get("duration", 0.0)),
            }
            for seg in parsed
            if isinstance(seg, dict) and str(seg.get("text", "")).strip()
        ]
    return [{"text": text, "start": 0.0, "duration": 0.0}]


def fetch_hf_space_transcript_sync(
    video_id: str,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Transcribe a video via a Whisper HF Space (stub tier).

    Two modes:
      - WHISPER_SPACE_ID set: call a public Space via gradio_client.
      - WHISPER_SPACE_URL set: POST to a self-hosted Gradio /api/predict.
    Neither configured -> WhisperClientError (cascade falls through).

    Raises:
        WhisperClientError: Missing config, timeout, HTTP error, bad response.
    """
    space_id = settings.whisper_space_id.strip()
    space_url = settings.whisper_space_url.strip()
    if not space_id and not space_url:
        raise WhisperClientError(
            "No Whisper Space configured. Set WHISPER_SPACE_ID (public Space via"
            " gradio_client) or WHISPER_SPACE_URL (self-hosted /api/predict)."
        )

    timeout = (
        timeout_seconds if timeout_seconds is not None else settings.whisper_space_timeout_seconds
    )

    if space_id:
        return _call_space_via_client(space_id, video_id, timeout)

    api_url = _build_space_url(space_url)
    body = {"data": [f"https://www.youtube.com/watch?v={video_id}"]}

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(api_url, json=body)
            resp.raise_for_status()
            raw = resp.json()
    except httpx.TimeoutException as exc:
        raise WhisperClientError(f"Whisper Space request timed out after {timeout}s") from exc
    except httpx.HTTPStatusError as exc:
        raise WhisperClientError(f"Whisper Space returned HTTP {exc.response.status_code}") from exc
    except Exception as exc:
        raise WhisperClientError(
            f"Whisper Space request failed: {type(exc).__name__}: {exc}"
        ) from exc

    return _parse_gradio_response(raw)
