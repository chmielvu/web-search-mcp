"""DuckDB-backed URL blocklist (patterns managed via add_blocklist_pattern)."""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

import duckdb

from ..models import WebSearchResult
from ..settings import settings

logger = logging.getLogger(__name__)
_regex_cache: re.Pattern[str] | None = None
_regex_lock = threading.Lock()
_db_lock = threading.Lock()


def _resolve_db_path() -> Path:
    configured = settings.blocklist_duckdb_path.strip()
    return Path(configured) if configured else Path("data") / "blocklist.duckdb"


def _translate_ublacklist_to_regex(pattern: str) -> str:
    """Translate uBlacklist globs, including optional subdomains, to regex."""
    value = pattern.strip()
    if not value:
        raise ValueError("blocklist pattern cannot be empty")
    scheme = ""
    body = value
    if body.startswith("*://"):
        scheme = r"https?://"
        body = body[4:]
    subdomain = ""
    if body.startswith("*."):
        subdomain = r"(?:[^/]+\.)?"
        body = body[2:]
    escaped = re.escape(body).replace(r"\*", ".*")
    return f"^{scheme}{subdomain}{escaped}$"


def _ensure_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE SEQUENCE IF NOT EXISTS blocklist_seq START 1")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS blocklist_patterns (
                id INTEGER PRIMARY KEY DEFAULT nextval('blocklist_seq'),
                glob_pattern VARCHAR NOT NULL,
                regex_pattern VARCHAR NOT NULL,
                source VARCHAR NOT NULL DEFAULT 'ublacklist',
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_blocklist_glob ON blocklist_patterns (glob_pattern)"
        )


def _compile_regex(db_path: Path) -> re.Pattern[str]:
    with duckdb.connect(str(db_path), read_only=True) as con:
        patterns = [
            row[0]
            for row in con.execute(
                "SELECT regex_pattern FROM blocklist_patterns WHERE active"
            ).fetchall()
        ]
    return re.compile("|".join(patterns), re.IGNORECASE) if patterns else re.compile(r"^$")


def _get_blocklist_regex() -> re.Pattern[str]:
    global _regex_cache
    if _regex_cache is not None:
        return _regex_cache
    with _regex_lock:
        if _regex_cache is None:
            path = _resolve_db_path()
            try:
                with _db_lock:
                    _ensure_db(path)
                _regex_cache = _compile_regex(path)
            except Exception:
                logger.warning(
                    "Blocklist database unavailable; disabling URL filtering", exc_info=True
                )
                _regex_cache = re.compile(r"^$")
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
    with _db_lock:
        _ensure_db(path)
        with duckdb.connect(str(path)) as con:
            exists = con.execute(
                "SELECT COUNT(*) FROM blocklist_patterns WHERE glob_pattern = ?",
                [glob_pattern],
            ).fetchone()[0]
            if exists:
                con.execute(
                    "UPDATE blocklist_patterns SET regex_pattern = ?, source = ?, active = TRUE WHERE glob_pattern = ?",
                    [regex_pattern, source, glob_pattern],
                )
            else:
                con.execute(
                    "INSERT INTO blocklist_patterns (glob_pattern, regex_pattern, source, active) VALUES (?, ?, ?, TRUE)",
                    [glob_pattern, regex_pattern, source],
                )
    reload_blocklist()
    return True


def remove_blocklist_pattern(glob_pattern: str) -> bool:
    path = _resolve_db_path()
    with _db_lock:
        _ensure_db(path)
        with duckdb.connect(str(path)) as con:
            count = con.execute(
                "SELECT COUNT(*) FROM blocklist_patterns WHERE glob_pattern = ? AND active",
                [glob_pattern],
            ).fetchone()[0]
            if count:
                con.execute(
                    "UPDATE blocklist_patterns SET active = FALSE WHERE glob_pattern = ?",
                    [glob_pattern],
                )
    if count:
        reload_blocklist()
    return bool(count)
