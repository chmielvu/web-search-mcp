"""YouTube transcript cascade orchestrator.

Tries multiple backends sequentially:
  1. yt-dlp player API (7-client rotation)
  2. Whisper ASR via HF ZeroGPU Space (if WHISPER_SPACE_URL configured)
  3. Legacy youtube-transcript-api (with proxy support)
  4. Transcript cache (checked before cascade in fetch_transcript_with_cache)

Each backend normalizes output to:
  [{"text": str, "start": float, "duration": float}, ...]
"""

from __future__ import annotations

import logging
from typing import Any

from ..settings import settings
from .models import YouTubeError, TranscriptBackendError
from .transcript import fetch_transcript_data, calculate_total_duration
from .yt_dlp_backend import ytdlp_extract_subtitles

logger = logging.getLogger(__name__)

_VALID_BACKENDS = ("auto", "ytdlp", "api")


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
        backend: "auto" (cascade), "ytdlp" (yt-dlp only), "api" (legacy only)

    Returns:
        Tuple of (segments, backend_used).

    Raises:
        TranscriptBackendError when all strategies fail
        ValueError when backend is not recognized
    """
    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"Unknown backend '{backend}'. Valid: {', '.join(_VALID_BACKENDS)}"
        )

    errors: list[str] = []

    # --- Layer 1: yt-dlp player API ---
    if backend in ("auto", "ytdlp"):
        try:
            segments = ytdlp_extract_subtitles(video_id, language=language)
            if segments:
                return segments, "ytdlp"
            if backend == "ytdlp":
                raise YouTubeError(
                    f"yt-dlp: no subtitles available for {video_id}"
                )
            logger.debug(
                "yt-dlp returned empty subtitles for %s, trying next", video_id
            )
        except ImportError:
            errors.append("yt-dlp: not installed")
            logger.debug("yt-dlp not installed, skipping")
        except YouTubeError as exc:
            errors.append(f"yt-dlp: {exc}")
            logger.debug("yt-dlp failed for %s: %s", video_id, exc)
        except Exception as exc:
            errors.append(f"yt-dlp: {type(exc).__name__}: {exc}")
            logger.debug("yt-dlp unexpected error for %s: %s", video_id, exc)

    # --- Layer 2: Whisper ASR (if WHISPER_SPACE_URL configured) ---
    if backend in ("auto",) and settings.whisper_space_url.strip():
        errors, segments = _try_whisper(video_id, errors)
        if segments is not None:
            return segments, "whisper"

    # --- Layer 3: Legacy youtube-transcript-api ---
    if backend in ("auto", "api"):
        try:
            segments = fetch_transcript_data(
                video_id, language=language, translate_to=translate_to
            )
            return segments, "api"
        except YouTubeError as exc:
            errors.append(f"api: {exc}")
            logger.debug("legacy api failed for %s: %s", video_id, exc)
        except Exception as exc:
            errors.append(f"api: {type(exc).__name__}: {exc}")
            logger.debug("legacy api unexpected error for %s: %s", video_id, exc)

    raise TranscriptBackendError(
        f"All transcript backends failed for video {video_id}. "
        f"Errors: {'; '.join(errors)}"
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
        return cached["segments"], "cache"

    segments, backend_used = fetch_transcript_cascade(
        video_id, language=language, translate_to=translate_to, backend=backend
    )
    cache.store(
        video_id,
        language,
        translate_to,
        segments,
        calculate_total_duration(segments),
    )
    return segments, backend_used
