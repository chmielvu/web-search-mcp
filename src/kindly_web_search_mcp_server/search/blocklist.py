"""SQLite-backed URL blocklist (patterns managed via add_blocklist_pattern)."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path

from ..models import WebSearchResult
from ..settings import settings

logger = logging.getLogger(__name__)
_regex_cache: re.Pattern[str] | None = None
_regex_lock = threading.Lock()
_db_lock = threading.Lock()


def _resolve_db_path() -> Path:
    configured = getattr(settings, "blocklist_sqlite_path", None) or getattr(
        settings, "blocklist_duckdb_path", ""
    )
    configured = configured.strip()
    return Path(configured) if configured else Path("data") / "blocklist.sqlite"


def _translate_ublacklist_to_regex(pattern: str) -> str:
    """Translate uBlacklist globs, including optional subdomains, to regex."""
    pattern = pattern.strip()
    if not pattern:
        return r"^$"

    # Normalize double slashes in URL schemes
    if pattern.startswith("*://"):
        scheme = r"https?://"
        pattern = pattern[4:]
    elif "://" in pattern:
        scheme_part, pattern = pattern.split("://", 1)
        scheme = f"^{re.escape(scheme_part)}://"
    else:
        scheme = r"^https?://"

    subdomain = ""
    if pattern.startswith("*."):
        subdomain = r"(?:[^/]+\.)?"
        pattern = pattern[2:]

    # Escape special regex chars except wildcard *
    parts = pattern.split("*")
    escaped_parts = [re.escape(p) for p in parts]
    escaped = ".*".join(escaped_parts)

    return f"{scheme}{subdomain}{escaped}$"


def _ensure_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        con = sqlite3.connect(str(db_path), timeout=10.0)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA busy_timeout=5000;")
        try:
            with con:
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS blocklist_patterns (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        glob_pattern TEXT NOT NULL UNIQUE,
                        regex_pattern TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'manual',
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
                con.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_blocklist_glob ON blocklist_patterns (glob_pattern);"
                )
        finally:
            con.close()


def _compile_regex(db_path: Path) -> re.Pattern[str]:
    con = sqlite3.connect(str(db_path), timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT regex_pattern FROM blocklist_patterns WHERE active = 1"
        ).fetchall()
        patterns = [row["regex_pattern"] for row in rows]
    finally:
        con.close()
    return re.compile("|".join(patterns), re.IGNORECASE) if patterns else re.compile(r"^$")


def _get_blocklist_regex() -> re.Pattern[str]:
    global _regex_cache
    if _regex_cache is None:
        with _regex_lock:
            if _regex_cache is None:
                db_path = _resolve_db_path()
                _ensure_db(db_path)
                _regex_cache = _compile_regex(db_path)
    return _regex_cache


def reload_blocklist() -> None:
    global _regex_cache
    with _regex_lock:
        _regex_cache = None
    _get_blocklist_regex()


def is_blocked_url(url: str) -> bool:
    return bool(url and _get_blocklist_regex().match(url))


def filter_blocked_results(results: list[WebSearchResult]) -> list[WebSearchResult]:
    return [result for result in results if not is_blocked_url(result.link)]


def add_blocklist_pattern(glob_pattern: str, source: str = "manual") -> bool:
    regex_pattern = _translate_ublacklist_to_regex(glob_pattern)
    path = _resolve_db_path()
    _ensure_db(path)
    with _db_lock:
        con = sqlite3.connect(str(path), timeout=10.0)
        try:
            with con:
                con.execute(
                    """
                    INSERT INTO blocklist_patterns (glob_pattern, regex_pattern, source, active)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(glob_pattern) DO UPDATE SET
                        regex_pattern = excluded.regex_pattern,
                        source = excluded.source,
                        active = 1
                    """,
                    (glob_pattern, regex_pattern, source),
                )
        finally:
            con.close()
    reload_blocklist()
    return True


def remove_blocklist_pattern(glob_pattern: str) -> bool:
    path = _resolve_db_path()
    _ensure_db(path)
    with _db_lock:
        con = sqlite3.connect(str(path), timeout=10.0)
        try:
            with con:
                cursor = con.execute(
                    "UPDATE blocklist_patterns SET active = 0 WHERE glob_pattern = ?",
                    (glob_pattern,),
                )
                count = cursor.rowcount
        finally:
            con.close()
    if count:
        reload_blocklist()
    return bool(count)
