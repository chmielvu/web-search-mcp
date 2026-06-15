"""DuckDB-backed transcript cache.

Separate .duckdb file (default duckdb_data/cache/transcript_cache.duckdb).
SHA256(video_id|language|translate_to)[:32] key, TTL expiry, thread-safe.

This module provides the low-level backend. The public TranscriptCache
facade + singleton + observability live in transcript_cache.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

TRANSCRIPT_CACHE_DEFAULT_TTL_SECONDS = int(
    os.environ.get("TRANSCRIPT_CACHE_TTL_SECONDS", "2592000")  # 30 days
)


class TranscriptDuckDBCache:
    """DuckDB implementation for the transcript cache.

    Supports:
    - deterministic composite key hashing (video_id + language + translate_to)
    - TTL based on created_at + ttl_seconds
    - transcript segments as JSON roundtrip
    - locked writes (lock held for entire connect+op)
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()

    def _resolve_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        from ..settings import settings

        return Path(settings.transcript_cache_duckdb_path)

    def _ensure_schema(self, con: duckdb.DuckDBPyConnection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS transcript_cache (
                id VARCHAR,
                video_id VARCHAR,
                language VARCHAR,
                translate_to VARCHAR,
                cache_key VARCHAR,
                transcript_json VARCHAR,
                segment_count BIGINT,
                duration_seconds DOUBLE,
                created_at VARCHAR,
                ttl_seconds BIGINT
            )
            """
        )
        try:
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_transcript_cache_key "
                "ON transcript_cache(cache_key)"
            )
        except Exception:
            pass

    def _compute_cache_key(
        self,
        video_id: str,
        language: str | None,
        translate_to: str | None,
    ) -> str:
        raw = f"{video_id}|{language or ''}|{translate_to or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def lookup(
        self,
        video_id: str,
        language: str | None = None,
        translate_to: str | None = None,
    ) -> dict[str, Any] | None:
        """Composite-key lookup with TTL check. Returns None on miss or expiry."""
        cache_key = self._compute_cache_key(video_id, language, translate_to)
        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            con = duckdb.connect(str(path))
            try:
                self._ensure_schema(con)
                row = con.execute(
                    """
                    SELECT
                        transcript_json,
                        segment_count,
                        duration_seconds,
                        created_at,
                        ttl_seconds
                    FROM transcript_cache
                    WHERE cache_key = ?
                    LIMIT 1
                    """,
                    [cache_key],
                ).fetchone()

                if not row:
                    return None

                (
                    transcript_json,
                    segment_count,
                    duration_seconds,
                    created_at_str,
                    ttl_seconds,
                ) = row

                if not created_at_str:
                    return None

                try:
                    created_at = datetime.fromisoformat(created_at_str)
                except Exception:
                    return None

                age_seconds = (datetime.now(UTC) - created_at).total_seconds()
                eff_ttl = (
                    int(ttl_seconds)
                    if ttl_seconds is not None
                    else TRANSCRIPT_CACHE_DEFAULT_TTL_SECONDS
                )

                if age_seconds > eff_ttl:
                    return None

                segments: list[dict[str, Any]] = []
                if transcript_json:
                    try:
                        parsed = json.loads(transcript_json)
                        if isinstance(parsed, list):
                            segments = parsed
                    except (json.JSONDecodeError, TypeError):
                        return None

                return {
                    "segments": segments,
                    "segment_count": int(segment_count or 0),
                    "duration_seconds": float(duration_seconds or 0),
                    "age_seconds": age_seconds,
                    "cached_at": created_at_str,
                }
            finally:
                con.close()

    def store(
        self,
        video_id: str,
        language: str | None,
        translate_to: str | None,
        segments: list[dict[str, Any]],
        duration_seconds: float,
        ttl_seconds: int | None = None,
    ) -> None:
        """Store transcript segments. Locked write, delete-then-insert."""
        cache_key = self._compute_cache_key(video_id, language, translate_to)

        if ttl_seconds is None:
            ttl_seconds = TRANSCRIPT_CACHE_DEFAULT_TTL_SECONDS

        entry_id = uuid.uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        transcript_json = json.dumps(segments)
        segment_count = len(segments)

        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            con = duckdb.connect(str(path))
            try:
                self._ensure_schema(con)
                con.execute(
                    "DELETE FROM transcript_cache WHERE cache_key = ?",
                    [cache_key],
                )
                con.execute(
                    """
                    INSERT INTO transcript_cache (
                        id,
                        video_id,
                        language,
                        translate_to,
                        cache_key,
                        transcript_json,
                        segment_count,
                        duration_seconds,
                        created_at,
                        ttl_seconds
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        entry_id,
                        video_id,
                        language or "",
                        translate_to or "",
                        cache_key,
                        transcript_json,
                        segment_count,
                        duration_seconds,
                        created_at,
                        ttl_seconds,
                    ],
                )
            finally:
                con.close()
