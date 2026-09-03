"""Deterministic normalization and quality diagnostics for transcripts."""

from __future__ import annotations

import math
from typing import Any

from ..heuristics.text_clean import clean_text_for_llm
from ..models import YouTubeTranscriptQuality


def normalize_transcript_segments(
    segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], YouTubeTranscriptQuality]:
    """Validate, clean, sort, and conservatively deduplicate transcript segments."""
    normalized: list[dict[str, Any]] = []
    malformed = 0
    duplicates = 0

    for segment in segments:
        if not isinstance(segment, dict):
            malformed += 1
            continue
        text = clean_text_for_llm(str(segment.get("text", "")), role="transcript")
        if not text:
            continue
        try:
            start = float(segment.get("start", 0.0))
            duration = float(segment.get("duration", 0.0))
        except (TypeError, ValueError):
            malformed += 1
            continue
        if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration < 0:
            malformed += 1
            continue
        normalized.append({"text": text, "start": start, "duration": duration})

    normalized.sort(key=lambda item: (item["start"], item["duration"], item["text"]))
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for segment in normalized:
        # Exact duplicates at the same timestamp are common in rolling captions.
        key = (segment["text"], round(segment["start"] * 10))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        deduplicated.append(segment)

    full_text = " ".join(item["text"] for item in deduplicated)
    quality = YouTubeTranscriptQuality(
        segment_count=len(deduplicated),
        word_count=len(full_text.split()),
        character_count=len(full_text),
        duplicate_segments_removed=duplicates,
        malformed_segments_removed=malformed,
    )
    return deduplicated, quality


def truncate_segments(
    segments: list[dict[str, Any]],
    max_chars: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Truncate at segment boundaries so JSON and rendered output agree.

    ``max_chars <= 0`` means unlimited: the full segment list is returned
    untruncated (mirrors the fetch pipeline's 0 = unlimited convention).
    """
    if max_chars <= 0:
        return segments, False
    selected: list[dict[str, Any]] = []
    used = 0
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        addition = len(text) + (1 if selected else 0)
        if selected and used + addition > max_chars:
            return selected, True
        if not selected and len(text) > max_chars:
            return [{**segment, "text": text[:max_chars].rstrip() + "…"}], True
        selected.append(segment)
        used += addition
    return selected, False
