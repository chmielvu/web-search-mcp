"""YouTube transcript acquisition over external scraper vendors.

Replaces the removed datacenter-IP backends (yt-dlp, Cloudflare/HF Whisper,
legacy youtube-transcript-api): every tier below runs on the vendor's egress,
so a VPS IP is never the failure mode.

Single entry points: ``fetch_transcript_cascade`` (Apify actor primary,
https://apify.com/supreme_coder/youtube-transcript-scraper, then Bright Data
YouTube Scraper API dataset ``gd_lk56epmy2i5g7lzu0k``,
https://docs.brightdata.com/api-reference/scrapers/social-media-apis/youtube-videos-collect-by-url)
and ``fetch_transcript_with_cache`` (SQLite cache first). Both return the
segment contract ``[{"text": str, "start": float, "duration": float}, ...]``.
"""

import logging
import math
from typing import Any

from ..utils.text_clean import clean_text_for_llm

logger = logging.getLogger(__name__)

_APIFY_DEFAULT_OUTPUT_FORMAT = "json"


class ScraperTranscriptError(RuntimeError):
    """Raised when an external transcript tier fails for a video."""

    pass


def _normalize_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate, clean, sort, and deduplicate raw vendor segments.

    Single validation step (subsumes the removed ``youtube/quality.py``
    normalizer): vendor parsers above only coerce shape.
    """

    normalized: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = clean_text_for_llm(str(segment.get("text", "")), role="transcript")
        if not text:
            continue
        try:
            start = float(segment.get("start", 0.0))
            duration = float(segment.get("duration", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration < 0:
            continue
        normalized.append({"text": text, "start": start, "duration": duration})

    normalized.sort(key=lambda item: (item["start"], item["duration"], item["text"]))
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for segment in normalized:
        # Exact duplicates at the same timestamp are common in rolling captions.
        key = (segment["text"], round(segment["start"] * 10))
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(segment)
    return deduplicated


def _coerce_segments(raw: Any) -> list[dict[str, Any]]:
    """Coerce a transcript array into segments; empty when unusable."""
    if not isinstance(raw, list):
        return []
    segments: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(item.get("start", 0.0))
            duration = float(item.get("duration", 0.0))
        except (TypeError, ValueError):
            continue
        segments.append({"text": text, "start": start, "duration": duration})
    return segments


def _coerce_plain_text(raw: Any) -> list[dict[str, Any]]:
    """Coerce a bare transcript string into one untimed segment."""
    text = str(raw or "").strip()
    if not text:
        return []
    return [{"text": text, "start": 0.0, "duration": 0.0}]


def calculate_total_duration(segments: list[dict[str, Any]]) -> float:
    """Calculate total video duration from transcript segments."""
    if not segments:
        return 0.0
    last_seg = segments[-1]
    return last_seg.get("start", 0.0) + last_seg.get("duration", 0.0)


def format_transcript_text(segments: list[dict[str, Any]]) -> str:
    """Format transcript as plain text (concatenated)."""
    return " ".join(seg.get("text", "").strip() for seg in segments if seg.get("text", "").strip())


def format_transcript_timestamped(segments: list[dict[str, Any]]) -> str:
    """Format transcript with timestamps [MM:SS]."""
    lines = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        start = seg.get("start", 0.0)
        minutes = int(start // 60)
        seconds = int(start % 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {text}")
    return "\n".join(lines)


def render_youtube_transcript_markdown(
    *,
    video_id: str,
    title: str | None,
    transcript_text: str,
    language: str,
    is_translated: bool,
    source_url: str,
    duration_seconds: float | None,
) -> str:
    """Render YouTube transcript to deterministic Markdown."""
    lines = ["# YouTube Video Transcript", ""]

    title_str = title or f"Video {video_id}"
    lines.append(f"Video: {title_str}")
    lines.append(f"URL: {source_url}")
    lines.append(f"Language: {language}")
    if is_translated:
        lines.append("(Translated)")
    if duration_seconds:
        mins = int(duration_seconds // 60)
        secs = int(duration_seconds % 60)
        lines.append(f"Duration: {mins}:{secs:02d}")

    lines.append("")
    lines.append("## Transcript")
    lines.append("")
    lines.append(transcript_text)
    lines.append("")

    return "\n".join(lines).strip() + "\n"


def parse_apify_transcript_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse one ``supreme_coder/youtube-transcript-scraper`` dataset item."""
    transcript = item.get("transcript")
    if isinstance(transcript, list):
        segments = _coerce_segments(transcript)
        if segments:
            return segments
    if isinstance(transcript, str):
        return _coerce_plain_text(transcript)
    return []


def parse_brightdata_transcript_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse one Bright Data YouTube video record into segments.

    The record carries ``transcript`` (raw text, verified in the API
    reference) and per the research report a ``formatted_transcript`` field;
    accept plain text, a segment list, or an SRT/VTT blob.
    """
    formatted = record.get("formatted_transcript")
    if isinstance(formatted, list):
        segments = _coerce_segments(formatted)
        if segments:
            return segments
    if isinstance(formatted, str) and formatted.strip():
        text = formatted.strip()
        if "\n" in text and "-->" in text:
            return _coerce_plain_text(text)
        return _coerce_plain_text(text)
    return _coerce_plain_text(record.get("transcript"))


def _apify_run_input(
    video_id: str,
    *,
    language: str | None,
    translate_to: str | None,
) -> dict[str, Any]:
    """Build the actor input for one video (json output, language fallback)."""
    languages = [language] if language else ["en"]
    run_input: dict[str, Any] = {
        "urls": [{"url": f"https://www.youtube.com/watch?v={video_id}"}],
        "outputFormat": _APIFY_DEFAULT_OUTPUT_FORMAT,
        "languages": languages,
    }
    if translate_to:
        run_input["translateTo"] = translate_to
    return run_input


async def apify_fetch_transcript(
    video_id: str,
    *,
    language: str | None = None,
    translate_to: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch transcript segments via the Apify YouTube transcript actor.

    Raises:
        ScraperTranscriptError: When the token/actor is unconfigured or the
            actor returns no usable transcript for the video.
    """
    from ..settings import settings
    from .remote_clients import ApifyClientError, get_apify_client

    client = get_apify_client()
    if client is None:
        raise ScraperTranscriptError("APIFY_API_TOKEN is not configured")
    run_input = _apify_run_input(video_id, language=language, translate_to=translate_to)
    try:
        items = await client.run_sync_get_dataset_items(settings.apify_youtube_actor, run_input)
    except ApifyClientError as exc:
        raise ScraperTranscriptError(f"apify: {exc}") from exc
    for item in items:
        segments = parse_apify_transcript_item(item)
        if segments:
            return segments
    raise ScraperTranscriptError(f"apify: no transcript returned for {video_id}")


def _brightdata_record_input(video_id: str, *, language: str | None) -> dict[str, Any]:
    """Build one collect-by-URL input row (supports transcription_language)."""
    row: dict[str, Any] = {"url": f"https://www.youtube.com/watch?v={video_id}"}
    if language:
        row["transcription_language"] = language
    return row


async def brightdata_fetch_transcript(
    video_id: str,
    *,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch transcript segments via the Bright Data YouTube Scraper API.

    Raises:
        ScraperTranscriptError: When the key is unconfigured or the record
            carries no usable transcript for the video.
    """
    from ..settings import settings
    from .brightdata_scraper import ScraperClientError, get_brightdata_scraper_client

    client = get_brightdata_scraper_client()
    if client is None:
        raise ScraperTranscriptError("BRIGHTDATA_API_KEY is not configured")
    dataset_id = settings.brightdata_youtube_dataset_id.strip()
    if not dataset_id:
        raise ScraperTranscriptError("BRIGHTDATA_YOUTUBE_DATASET_ID is not configured")
    try:
        records = await client.scrape_records(
            dataset_id, [_brightdata_record_input(video_id, language=language)]
        )
    except ScraperClientError as exc:
        raise ScraperTranscriptError(f"brightdata: {exc}") from exc
    for record in records:
        segments = parse_brightdata_transcript_record(record)
        if segments:
            return segments
    raise ScraperTranscriptError(f"brightdata: no transcript returned for {video_id}")


_VALID_BACKENDS = ("auto", "apify", "brightdata")


async def fetch_transcript_cascade(
    video_id: str,
    *,
    language: str | None = None,
    translate_to: str | None = None,
    backend: str = "auto",
) -> tuple[list[dict[str, Any]], str]:
    """Fetch transcript segments (Apify primary, Bright Data fallback).

    Args:
        video_id: YouTube video ID.
        language: Preferred language code.
        translate_to: Target language for translation (Apify tier only).
        backend: "auto" (cascade), "apify" (Apify only),
            "brightdata" (Bright Data only).

    Returns:
        Tuple of (segments, backend_used).

    Raises:
        ScraperTranscriptError: When all tiers fail, or when an explicit
            backend is selected but its credentials are missing.
        ValueError: When backend is not recognized.
    """
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}. Valid: {', '.join(_VALID_BACKENDS)}")

    errors: list[str] = []

    # --- Tier 1: Apify YouTube transcript actor ---
    if backend in ("auto", "apify"):
        from .remote_clients import get_apify_client

        if get_apify_client() is not None:
            try:
                segments = await apify_fetch_transcript(
                    video_id, language=language, translate_to=translate_to
                )
                if segments:
                    return segments, "apify"
                errors.append("apify: empty transcript")
            except ScraperTranscriptError as exc:
                errors.append(str(exc))
                logger.debug("apify failed for %s: %s", video_id, exc)
            except Exception as exc:
                errors.append(f"apify: {type(exc).__name__}: {exc}")
                logger.debug("apify unexpected error for %s: %s", video_id, exc)
        elif backend == "apify":
            raise ScraperTranscriptError("APIFY_API_TOKEN must be configured")

    # --- Tier 2: Bright Data YouTube Scraper API ---
    if backend in ("auto", "brightdata"):
        from .brightdata_scraper import get_brightdata_scraper_client

        if get_brightdata_scraper_client() is not None:
            try:
                segments = await brightdata_fetch_transcript(video_id, language=language)
                if segments:
                    return segments, "brightdata"
                errors.append("brightdata: empty transcript")
            except ScraperTranscriptError as exc:
                errors.append(str(exc))
                logger.debug("brightdata failed for %s: %s", video_id, exc)
    raise ScraperTranscriptError(
        f"All transcript backends failed for video {video_id}. Errors: {'; '.join(errors)}"
    )


async def fetch_transcript_with_cache(
    video_id: str,
    *,
    language: str | None = None,
    translate_to: str | None = None,
    backend: str = "auto",
) -> tuple[list[dict[str, Any]], str]:
    """Check the transcript cache first, then the scraper tiers.

    Returns (segments, backend_used) where backend_used may be 'cache' on hit.
    """
    from ..cache import get_transcript_cache

    cache = get_transcript_cache()
    cached = cache.lookup(video_id, language=language, translate_to=translate_to)
    if cached:
        return _normalize_segments(cached["segments"]), "cache"

    segments, backend_used = await fetch_transcript_cascade(
        video_id, language=language, translate_to=translate_to, backend=backend
    )
    normalized = _normalize_segments(segments)
    cache.store(
        video_id,
        language,
        translate_to,
        normalized,
        calculate_total_duration(normalized),
    )
    return normalized, backend_used
