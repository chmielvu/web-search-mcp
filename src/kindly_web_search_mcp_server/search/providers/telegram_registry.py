"""SQLite-backed Telegram channel registry.

Stores curated channel list from goq/telegram-list, palark/awesome-devops-telegram,
and manual entries. Maps channels to search intents for intent-based routing.

Follows the page_sqlite.py pattern: separate .sqlite file, threading.Lock,
_ensure_schema(), lazy path resolution.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...settings import settings


@dataclass
class TelegramChannelEntry:
    """A channel entry for the registry."""

    username: str
    title: str
    entry_type: str = "channel"  # "channel" | "group"
    language: str = ""
    intent: str = "general"
    category: str = ""
    member_count: int | None = None
    source: str = "manual"
    source_url: str = ""
    active: bool = True


class TelegramRegistrySQLite:
    """SQLite implementation for the Telegram channel registry."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()

    def _resolve_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        path_str = getattr(settings, "telegram_registry_sqlite_path", None) or getattr(
            settings, "telegram_registry_duckdb_path", "duckdb_data/telegram/registry.sqlite"
        )
        return Path(path_str)

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
                CREATE TABLE IF NOT EXISTS telegram_channels (
                    username     TEXT PRIMARY KEY,
                    title        TEXT NOT NULL,
                    entry_type   TEXT NOT NULL DEFAULT 'channel',
                    language     TEXT DEFAULT '',
                    intent       TEXT DEFAULT 'general',
                    category     TEXT DEFAULT '',
                    member_count INTEGER,
                    source       TEXT NOT NULL DEFAULT 'manual',
                    source_url   TEXT DEFAULT '',
                    active       INTEGER DEFAULT 1,
                    joined       INTEGER DEFAULT 0,
                    joined_at    TEXT,
                    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_tg_intent ON telegram_channels (intent, active);"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_tg_joined ON telegram_channels (joined, active);"
            )

    def upsert_channels(self, entries: list[TelegramChannelEntry]) -> int:
        """Insert or update channels. Returns count of upserted entries."""
        if not entries:
            return 0
        now_iso = datetime.now(UTC).isoformat()
        with self._lock:
            con = self._get_connection()
            try:
                self._ensure_schema(con)
                with con:
                    for e in entries:
                        con.execute(
                            """
                            INSERT INTO telegram_channels (
                                username, title, entry_type, language, intent,
                                category, member_count, source, source_url, active, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(username) DO UPDATE SET
                                title = excluded.title,
                                entry_type = excluded.entry_type,
                                language = excluded.language,
                                intent = excluded.intent,
                                category = excluded.category,
                                member_count = excluded.member_count,
                                source = excluded.source,
                                source_url = excluded.source_url,
                                active = excluded.active,
                                updated_at = excluded.updated_at
                            """,
                            (
                                e.username.lstrip("@").lower(),
                                e.title,
                                e.entry_type,
                                e.language,
                                e.intent,
                                e.category,
                                e.member_count,
                                e.source,
                                e.source_url,
                                1 if e.active else 0,
                                now_iso,
                            ),
                        )
                return len(entries)
            finally:
                con.close()

    def get_channels_for_intent(self, intent: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get top channels for an intent, ordered by member_count desc."""
        with self._lock:
            con = self._get_connection()
            try:
                self._ensure_schema(con)
                rows = con.execute(
                    """
                    SELECT username, title, entry_type, language, intent, category,
                           member_count, source, joined
                    FROM telegram_channels
                    WHERE intent = ? AND active = 1
                    ORDER BY member_count DESC NULLS LAST
                    LIMIT ?
                    """,
                    (intent, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                con.close()

    def mark_joined(self, username: str) -> None:
        """Mark a channel as joined by the account."""
        clean_user = username.lstrip("@").lower()
        now_iso = datetime.now(UTC).isoformat()
        with self._lock:
            con = self._get_connection()
            try:
                self._ensure_schema(con)
                with con:
                    con.execute(
                        "UPDATE telegram_channels SET joined = 1, joined_at = ? WHERE username = ?",
                        (now_iso, clean_user),
                    )
            finally:
                con.close()

    def get_unjoined_for_intent(self, intent: str, limit: int = 10) -> list[str]:
        """Get channel usernames we haven't joined yet."""
        with self._lock:
            con = self._get_connection()
            try:
                self._ensure_schema(con)
                rows = con.execute(
                    """
                    SELECT username FROM telegram_channels
                    WHERE intent = ? AND active = 1 AND joined = 0
                    ORDER BY member_count DESC NULLS LAST
                    LIMIT ?
                    """,
                    (intent, limit),
                ).fetchall()
                return [r["username"] for r in rows]
            finally:
                con.close()

    def total_channels(self) -> int:
        """Return total channel count."""
        with self._lock:
            con = self._get_connection()
            try:
                self._ensure_schema(con)
                row = con.execute("SELECT COUNT(*) as cnt FROM telegram_channels").fetchone()
                return row["cnt"] if row else 0
            finally:
                con.close()


# Alias for compatibility
TelegramRegistryDuckDB = TelegramRegistrySQLite
