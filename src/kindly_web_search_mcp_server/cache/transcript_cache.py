"""Transcript cache facade with observability.

Caches YouTube transcript segments by composite key
(video_id + language + translate_to), with TTL and DuckDB backend.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..telemetry import record_cache_lookup
from .observability import emit_cache_lookup_event, emit_cache_store_event
from .transcript_sqlite import (
    TRANSCRIPT_CACHE_DEFAULT_TTL_SECONDS,
    TranscriptSQLiteCache as _TranscriptSQLiteCache,
)

logger = logging.getLogger(__name__)


class TranscriptCache:
    """SQLite-backed transcript cache.

    Thin facade over TranscriptSQLiteCache (in transcript_sqlite.py) that
    adds observability logging and telemetry recording.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._backend = _TranscriptSQLiteCache(db_path=db_path)

    def lookup(
        self,
        video_id: str,
        language: str | None = None,
        translate_to: str | None = None,
    ) -> dict[str, Any] | None:
        """Look up cached transcript for a video + language combo."""
        start_time = time.time()
        result = self._backend.lookup(video_id, language=language, translate_to=translate_to)
        duration = time.time() - start_time

        if result is None:
            record_cache_lookup(cache_type="transcript", hit=False, duration_seconds=duration)
            emit_cache_lookup_event(
                logger,
                "transcript",
                "miss",
                duration_ms=round(duration * 1000, 3),
                video_id=video_id,
            )
            return None

        record_cache_lookup(cache_type="transcript", hit=True, duration_seconds=duration)
        emit_cache_lookup_event(
            logger,
            "transcript",
            "hit",
            duration_ms=round(duration * 1000, 3),
            age_seconds=round(result.get("age_seconds", 0), 3),
            segment_count=result.get("segment_count", 0),
            video_id=video_id,
        )

        return result

    def store(
        self,
        video_id: str,
        language: str | None,
        translate_to: str | None,
        segments: list[dict[str, Any]],
        duration_seconds: float,
        ttl_seconds: int | None = None,
    ) -> None:
        """Store transcript segments via DuckDB backend."""
        try:
            self._backend.store(
                video_id=video_id,
                language=language,
                translate_to=translate_to,
                segments=segments,
                duration_seconds=duration_seconds,
                ttl_seconds=ttl_seconds,
            )
            logger.debug(
                "Stored transcript cache entry (video_id=%s, segments=%d)",
                video_id,
                len(segments),
            )
            emit_cache_store_event(
                logger,
                "transcript",
                "ok",
                ttl_seconds=ttl_seconds or TRANSCRIPT_CACHE_DEFAULT_TTL_SECONDS,
                segment_count=len(segments),
                video_id=video_id,
            )
        except Exception as exc:
            logger.warning("Failed to store transcript cache entry: %s", exc)
            emit_cache_store_event(
                logger,
                "transcript",
                "error",
                error_type=type(exc).__name__,
                video_id=video_id,
            )


# Singleton instance (lazy init)
_TRANSCRIPT_CACHE: TranscriptCache | None = None


def get_transcript_cache(db_path: str | None = None) -> TranscriptCache:
    """Get or create the transcript cache singleton."""
    global _TRANSCRIPT_CACHE
    if _TRANSCRIPT_CACHE is None:
        from ..settings import settings

        actual_path = db_path or settings.transcript_cache_sqlite_path
        _TRANSCRIPT_CACHE = TranscriptCache(db_path=actual_path)
        logger.info("Initialized transcript cache at %s", actual_path)
    return _TRANSCRIPT_CACHE
