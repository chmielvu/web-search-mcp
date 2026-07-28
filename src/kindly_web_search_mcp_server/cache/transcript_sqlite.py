"""SQLite-backed YouTube transcript cache with FTS5 text search.

Separate .sqlite file (default duckdb_data/cache/transcript_cache.sqlite).
SHA256(video_id|language|translate_to)[:32] key, thread-safe SQLite WAL mode,
and FTS5 virtual table for searching transcript text. Note: TTL column removed
per requirements.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..settings import settings

TRANSCRIPT_CACHE_DEFAULT_TTL_SECONDS = int(
    os.environ.get("TRANSCRIPT_CACHE_TTL_SECONDS", "2592000")  # 30 days
)
logger = logging.getLogger(__name__)


class TranscriptSQLiteCache:
    """SQLite implementation for the transcript cache using WAL mode & FTS5."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()

    def _resolve_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        return Path(settings.transcript_cache_sqlite_path)

    def _get_connection(self) -> sqlite3.Connection:
        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path), timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA busy_timeout=5000;")
        return con

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
        with con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS transcript_cache (
                    cache_key TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    language TEXT,
                    translate_to TEXT,
                    transcript_json TEXT NOT NULL,
                    segment_count INTEGER NOT NULL,
                    duration_seconds REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    ttl_seconds INTEGER NOT NULL DEFAULT 2592000
                );
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_transcript_video_id ON transcript_cache(video_id);"
            )
            con.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
                    video_id,
                    text_content,
                    content='transcript_cache',
                    content_rowid='rowid'
                );
                """
            )

    def ensure_store_schema(self, con: sqlite3.Connection) -> None:
        try:
            self._ensure_schema(con)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Transcript cache schema creation failed: %s", exc)

    def _compute_cache_key(
        self,
        video_id: str,
        language: str | None = None,
        translate_to: str | None = None,
    ) -> str:
        raw = f"{video_id}|{language or ''}|{translate_to or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def lookup(
        self,
        video_id: str,
        language: str | None = None,
        translate_to: str | None = None,
    ) -> dict[str, Any] | None:
        return self._lookup_sync(video_id, language, translate_to)

    async def alookup(
        self,
        video_id: str,
        language: str | None = None,
        translate_to: str | None = None,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._lookup_sync, video_id, language, translate_to)

    def _lookup_sync(
        self,
        video_id: str,
        language: str | None = None,
        translate_to: str | None = None,
    ) -> dict[str, Any] | None:
        cache_key = self._compute_cache_key(video_id, language, translate_to)
        with self._lock:
            try:
                con = self._get_connection()
                try:
                    self._ensure_schema(con)
                    row = con.execute(
                        """
                        SELECT cache_key, video_id, language, translate_to, transcript_json,
                               segment_count, duration_seconds, created_at, ttl_seconds
                        FROM transcript_cache
                        WHERE cache_key = ?
                        """,
                        (cache_key,),
                    ).fetchone()

                    if not row:
                        return None

                    # Check TTL
                    created_at_str = row["created_at"]
                    ttl_seconds = row["ttl_seconds"]
                    try:
                        created_at = datetime.fromisoformat(created_at_str)
                    except ValueError:
                        return None

                    age_seconds = (datetime.now(UTC) - created_at).total_seconds()
                    if age_seconds > ttl_seconds:
                        return None

                    try:
                        transcript = json.loads(row["transcript_json"])
                    except json.JSONDecodeError:
                        return None

                    return {
                        "cache_key": row["cache_key"],
                        "video_id": row["video_id"],
                        "language": row["language"],
                        "translate_to": row["translate_to"],
                        "transcript": transcript,
                        "segments": transcript,
                        "segment_count": row["segment_count"],
                        "duration_seconds": row["duration_seconds"],
                        "created_at": row["created_at"],
                        "age_seconds": age_seconds,
                    }
                finally:
                    con.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("transcript_cache lookup failed for %s: %s", video_id, exc)
                return None

    def store(
        self,
        video_id: str,
        language: str | None = None,
        translate_to: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        duration_seconds: float = 0.0,
        ttl_seconds: int | None = None,
    ) -> None:
        transcript = segments or []
        self._store_sync(video_id, transcript, language, translate_to, ttl_seconds)

    async def astore(
        self,
        video_id: str,
        language: str | None = None,
        translate_to: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        duration_seconds: float = 0.0,
        ttl_seconds: int | None = None,
    ) -> None:
        transcript = segments or []
        await asyncio.to_thread(
            self._store_sync, video_id, transcript, language, translate_to, ttl_seconds
        )

    def _store_sync(
        self,
        video_id: str,
        transcript: list[dict[str, Any]],
        language: str | None = None,
        translate_to: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        cache_key = self._compute_cache_key(video_id, language, translate_to)
        now_iso = datetime.now(UTC).isoformat()
        transcript_json = json.dumps(transcript)
        actual_ttl = (
            ttl_seconds if ttl_seconds is not None else TRANSCRIPT_CACHE_DEFAULT_TTL_SECONDS
        )

        # Compute duration & segment count
        segment_count = len(transcript)
        duration_seconds = 0.0
        text_pieces = []
        for seg in transcript:
            if isinstance(seg, dict):
                start = float(seg.get("start", 0.0))
                duration = float(seg.get("duration", 0.0))
                end = start + duration
                if end > duration_seconds:
                    duration_seconds = end
                text = seg.get("text", "")
                if text:
                    text_pieces.append(str(text))

        full_text = " ".join(text_pieces)

        with self._lock:
            try:
                con = self._get_connection()
                try:
                    self.ensure_store_schema(con)
                    with con:
                        con.execute(
                            """
                            INSERT INTO transcript_cache (
                                cache_key, video_id, language, translate_to, transcript_json,
                                segment_count, duration_seconds, created_at, ttl_seconds
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(cache_key) DO UPDATE SET
                                transcript_json = excluded.transcript_json,
                                segment_count = excluded.segment_count,
                                duration_seconds = excluded.duration_seconds,
                                created_at = excluded.created_at,
                                ttl_seconds = excluded.ttl_seconds
                            """,
                            (
                                cache_key,
                                video_id,
                                language,
                                translate_to,
                                transcript_json,
                                segment_count,
                                duration_seconds,
                                now_iso,
                                actual_ttl,
                            ),
                        )
                        # Sync FTS index
                        con.execute(
                            """
                            INSERT OR REPLACE INTO transcript_fts (rowid, video_id, text_content)
                            SELECT rowid, video_id, ? FROM transcript_cache WHERE cache_key = ?
                            """,
                            (full_text, cache_key),
                        )
                finally:
                    con.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("transcript_cache store failed for %s: %s", video_id, exc)

    def search_transcripts(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """FTS5 search over stored transcripts."""
        with self._lock:
            try:
                con = self._get_connection()
                try:
                    self._ensure_schema(con)
                    rows = con.execute(
                        """
                        SELECT c.cache_key, c.video_id, c.language, c.translate_to,
                               c.transcript_json, c.segment_count, c.duration_seconds, c.created_at
                        FROM transcript_fts f
                        JOIN transcript_cache c ON f.rowid = c.rowid
                        WHERE transcript_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (query, limit),
                    ).fetchall()

                    results = []
                    for row in rows:
                        try:
                            t_data = json.loads(row["transcript_json"])
                        except json.JSONDecodeError:
                            t_data = []
                        results.append(
                            {
                                "cache_key": row["cache_key"],
                                "video_id": row["video_id"],
                                "language": row["language"],
                                "translate_to": row["translate_to"],
                                "transcript": t_data,
                                "segment_count": row["segment_count"],
                                "duration_seconds": row["duration_seconds"],
                                "created_at": row["created_at"],
                            }
                        )
                    return results
                finally:
                    con.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("transcript_cache search failed for query %r: %s", query, exc)
                return []
