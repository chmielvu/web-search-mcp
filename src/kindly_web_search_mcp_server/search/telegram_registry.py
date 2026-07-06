"""DuckDB-backed Telegram channel registry.

Stores curated channel list from goq/telegram-list, palark/awesome-devops-telegram,
and manual entries. Maps channels to search intents for intent-based routing.

Follows the page_duckdb.py pattern: separate .duckdb file, threading.Lock,
_ensure_schema(), lazy path resolution.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb


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


class TelegramRegistryDuckDB:
    """DuckDB implementation for the Telegram channel registry."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()

    def _resolve_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        from ..settings import settings

        return Path(settings.telegram_registry_duckdb_path)

    def _ensure_schema(self, con: duckdb.DuckDBPyConnection) -> None:
        con.execute("""
            CREATE TABLE IF NOT EXISTS telegram_channels (
                username VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                channel_id BIGINT,
                entry_type VARCHAR NOT NULL DEFAULT 'channel',
                language VARCHAR DEFAULT '',
                intent VARCHAR DEFAULT 'general',
                category VARCHAR DEFAULT '',
                member_count INTEGER,
                source VARCHAR NOT NULL DEFAULT 'manual',
                source_url VARCHAR DEFAULT '',
                active BOOLEAN DEFAULT TRUE,
                joined BOOLEAN DEFAULT FALSE,
                joined_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def upsert_channels(self, entries: list[TelegramChannelEntry]) -> int:
        """Insert or update channels. Returns count of upserted entries."""
        with self._lock:
            path = self._resolve_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            con = duckdb.connect(str(path))
            self._ensure_schema(con)
            count = 0
            for e in entries:
                con.execute(
                    """
                    INSERT OR REPLACE INTO telegram_channels
                    (username, title, entry_type, language, intent, category,
                     member_count, source, source_url, active, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                    [
                        e.username,
                        e.title,
                        e.entry_type,
                        e.language,
                        e.intent,
                        e.category,
                        e.member_count,
                        e.source,
                        e.source_url,
                        e.active,
                    ],
                )
                count += 1
            con.close()
            return count

    def get_channels_for_intent(self, intent: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get top channels for an intent, ordered by member_count desc."""
        with self._lock:
            path = self._resolve_path()
            if not path.exists():
                return []
            con = duckdb.connect(str(path), read_only=True)
            rows = con.execute(
                """
                SELECT username, title, entry_type, language, member_count
                FROM telegram_channels
                WHERE intent = ? AND active = TRUE
                ORDER BY member_count DESC NULLS LAST
                LIMIT ?
            """,
                [intent, limit],
            ).fetchall()
            con.close()
            return [
                {"username": r[0], "title": r[1], "type": r[2], "language": r[3], "members": r[4]}
                for r in rows
            ]

    def mark_joined(self, username: str) -> None:
        """Mark a channel as joined by the account."""
        with self._lock:
            path = self._resolve_path()
            if not path.exists():
                return
            con = duckdb.connect(str(path))
            self._ensure_schema(con)
            con.execute(
                """
                UPDATE telegram_channels
                SET joined = TRUE, joined_at = CURRENT_TIMESTAMP
                WHERE username = ?
            """,
                [username],
            )
            con.close()

    def get_unjoined_for_intent(self, intent: str, limit: int = 10) -> list[str]:
        """Get channel usernames we haven't joined yet."""
        with self._lock:
            path = self._resolve_path()
            if not path.exists():
                return []
            con = duckdb.connect(str(path), read_only=True)
            rows = con.execute(
                """
                SELECT username FROM telegram_channels
                WHERE intent = ? AND active = TRUE AND joined = FALSE
                ORDER BY member_count DESC NULLS LAST
                LIMIT ?
            """,
                [intent, limit],
            ).fetchall()
            con.close()
            return [r[0] for r in rows]

    def total_channels(self) -> int:
        """Return total channel count."""
        with self._lock:
            path = self._resolve_path()
            if not path.exists():
                return 0
            con = duckdb.connect(str(path), read_only=True)
            count = con.execute("SELECT COUNT(*) FROM telegram_channels").fetchone()[0]  # type: ignore[index]
            con.close()
            return count
