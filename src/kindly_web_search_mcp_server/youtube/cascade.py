"""YouTube transcript cascade orchestrator.

Tries multiple backends sequentially:
  1. yt-dlp player API (7-client rotation)
  2. Cloudflare Workers AI Whisper (if CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN set)
  3. Whisper ASR via HF ZeroGPU Space (if WHISPER_SPACE_URL configured)
  4. Legacy youtube-transcript-api (with proxy support)
  5. Transcript cache (checked before cascade in fetch_transcript_with_cache)

Each backend normalizes output to:
  [{"text": str, "start": float, "duration": float}, ...]
"""

from __future__ import annotations

import logging
from typing import Any

from ..settings import settings
from .models import YouTubeError, TranscriptBackendError
from .transcript import fetch_transcript_data, calculate_total_duration
from .cf_whisper import CfWhisperError, _transcribe_sync
from .yt_dlp_backend import ytdlp_extract_subtitles
from .quality import normalize_transcript_segments

logger = logging.getLogger(__name__)

_VALID_BACKENDS = ("auto", "ytdlp", "vps_whisper", "cf_whisper", "whisper", "api")


def fetch_transcript_cascade(
    video_id: str,
    *,
    language: str | None = None,
    translate_to: str | None = None,
    backend: str = "auto",
) -> tuple[list[dict[str, Any]], str]:
    """Fetch transcript using cascade strategy.

    Args:
        video_id: YouTube video ID
        language: Preferred language code
        translate_to: Target language for translation
        backend: "auto" (cascade), "ytdlp" (yt-dlp only), "cf_whisper" (Cloudflare only),
                 "api" (legacy only)

    Returns:
        Tuple of (segments, backend_used).

    Raises:
        TranscriptBackendError when all strategies fail
        ValueError when backend is not recognized
    """
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"Unknown backend '{backend}'. Valid: {', '.join(_VALID_BACKENDS)}")

    errors: list[str] = []

    # --- Layer 1: yt-dlp player API ---
    if backend in ("auto", "ytdlp"):
        try:
            segments = ytdlp_extract_subtitles(video_id, language=language)
            if segments:
                return segments, "ytdlp"
            if backend == "ytdlp":
                raise YouTubeError(f"yt-dlp: no subtitles available for {video_id}")
            logger.debug("yt-dlp returned empty subtitles for %s, trying next", video_id)
        except ImportError:
            errors.append("yt-dlp: not installed")
            logger.debug("yt-dlp not installed, skipping")
        except YouTubeError as exc:
            errors.append(f"yt-dlp: {exc}")
            logger.debug("yt-dlp failed for %s: %s", video_id, exc)
        except Exception as exc:
            errors.append(f"yt-dlp: {type(exc).__name__}: {exc}")
            logger.debug("yt-dlp unexpected error for %s: %s", video_id, exc)

    # --- Layer 2: Whisper VPS Service (if WHISPER_VPS_URL configured) ---
    if backend in ("auto", "vps_whisper"):
        vps_url = settings.whisper_vps_url.strip()
        if vps_url:
            try:
                from .vps_whisper import fetch_vps_whisper_transcript_sync, VpsWhisperError

                segments = fetch_vps_whisper_transcript_sync(
                    video_id,
                    language=language,
                    task="translate" if translate_to else "transcribe",
                )
                if segments:
                    return segments, "vps_whisper"
                if backend == "vps_whisper":
                    raise VpsWhisperError(f"VPS Whisper returned empty for {video_id}")
            except Exception as exc:
                errors.append(f"vps_whisper: {exc}")
                logger.debug("VPS Whisper failed for %s: %s", video_id, exc)
        elif backend == "vps_whisper":
            raise YouTubeError("WHISPER_VPS_URL must be configured")

    # --- Layer 2: Cloudflare Workers AI Whisper ---
    if backend in ("auto", "cf_whisper"):
        cf_id = settings.cf_whisper_account_id.strip()
        cf_token = settings.cf_whisper_api_token.strip()
        if cf_id and cf_token:
            try:
                segments = _transcribe_sync(
                    video_id,
                    language=language,
                    task="translate" if translate_to else "transcribe",
                )
                if segments:
                    return segments, "cf_whisper"
                if backend == "cf_whisper":
                    raise CfWhisperError(f"Cloudflare Whisper returned empty for {video_id}")
            except CfWhisperError as exc:
                errors.append(f"cf_whisper: {exc}")
                logger.debug("Cloudflare Whisper failed for %s: %s", video_id, exc)
            except Exception as exc:
                errors.append(f"cf_whisper: {type(exc).__name__}: {exc}")
                logger.debug("Cloudflare Whisper unexpected error for %s: %s", video_id, exc)
        elif backend == "cf_whisper":
            raise YouTubeError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be configured")

    # --- Layer 3: Whisper ASR (if WHISPER_SPACE_URL configured) ---
    if backend in ("auto",) and settings.whisper_space_url.strip():
        errors, segments = _try_whisper(video_id, errors)
        if segments is not None:
            return segments, "whisper"

    # --- Layer 4: Legacy youtube-transcript-api ---
    if backend in ("auto", "api"):
        try:
            segments = fetch_transcript_data(video_id, language=language, translate_to=translate_to)
            return segments, "api"
        except YouTubeError as exc:
            errors.append(f"api: {exc}")
            logger.debug("legacy api failed for %s: %s", video_id, exc)
        except Exception as exc:
            errors.append(f"api: {type(exc).__name__}: {exc}")
            logger.debug("legacy api unexpected error for %s: %s", video_id, exc)

    raise TranscriptBackendError(
        f"All transcript backends failed for video {video_id}. Errors: {'; '.join(errors)}"
    )


def _try_whisper(
    video_id: str,
    errors: list[str],
) -> tuple[list[str], list[dict[str, Any]] | None]:
    """Try Whisper ASR backend. Returns (updated_errors, segments_or_none)."""
    try:
        from .whisper_client import fetch_whisper_transcript_sync
        from .whisper_client import WhisperClientError

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        segments = fetch_whisper_transcript_sync(
            video_url,
            timeout_seconds=settings.whisper_space_timeout_seconds,
        )
        if segments:
            return errors, segments
        logger.debug("Whisper returned empty for %s", video_id)
    except WhisperClientError as exc:
        errors.append(f"whisper: {exc}")
        logger.debug("Whisper failed for %s: %s", video_id, exc)
    except Exception as exc:
        errors.append(f"whisper: {type(exc).__name__}: {exc}")
        logger.debug("Whisper unexpected error for %s: %s", video_id, exc)
    return errors, None


def fetch_transcript_with_cache(
    video_id: str,
    *,
    language: str | None = None,
    translate_to: str | None = None,
    backend: str = "auto",
) -> tuple[list[dict[str, Any]], str]:
    """Check transcript cache first, then cascade backends.

    Returns (segments, backend_used) where backend_used may be 'cache' on hit.
    """
    from ..cache import get_transcript_cache

    cache = get_transcript_cache()
    cached = cache.lookup(video_id, language=language, translate_to=translate_to)
    if cached:
        normalized, _ = normalize_transcript_segments(cached["segments"])
        return normalized, "cache"

    segments, backend_used = fetch_transcript_cascade(
        video_id, language=language, translate_to=translate_to, backend=backend
    )
    normalized, _ = normalize_transcript_segments(segments)
    cache.store(
        video_id,
        language,
        translate_to,
        normalized,
        calculate_total_duration(normalized),
    )
    return normalized, backend_used
