"""SQLite-backed URL pattern list (patterns managed via add_blocklist_pattern).

Supports:
- uBlacklist-style glob patterns (e.g. ``*://*.example.com/*``)
- plain-domain subscriptions (``example.com`` from SSSS-style lists)
- fast set-based host matching (no regex backtracking on 100k+ patterns)
- import of community subscriptions (SSSS, HUGE-AI, Bad Websites)

Matching strategy:
- Pure-domain globs (``*://[*.]domain[/[*]]``) are compiled into host sets:
  ``exact_hosts`` (only the host) and ``sub_hosts`` (host + subdomains).
  Lookups are O(1) set membership — safe at 400k+ domains.
- Complex patterns (ports, partial paths, wildcard hosts) fall back to one
  alternation regex.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ..models import WebSearchResult
from ..settings import settings

logger = logging.getLogger(__name__)

_index_cache: "BlocklistIndex | None" = None
_index_lock = threading.Lock()
_db_lock = threading.Lock()

# Community subscriptions, keyed by stable name.
KNOWN_SUBSCRIPTIONS: dict[str, dict[str, str]] = {
    "ssss": {
        "name": "Super SEO Spam Suppressor (SSSS)",
        "url": "https://raw.githubusercontent.com/NotaInutilis/Super-SEO-Spam-Suppressor/main/domains.txt",
    },
    "huge-ai": {
        "name": "uBlockOrigin & uBlacklist HUGE AI Blocklist",
        "url": "https://raw.githubusercontent.com/laylavish/uBlockOrigin-HUGE-AI-Blocklist/main/list_uBlacklist.txt",
    },
    "bad-websites": {
        "name": "Bad Website Blocklist (AI & SEO spam)",
        "url": "https://raw.githubusercontent.com/popcar2/BadWebsiteBlocklist/main/uBlacklist.txt",
    },
}


@dataclass
class BlocklistIndex:
    """Prepared matching state.

    ``exact_hosts`` blocks only that host; ``sub_hosts`` blocks the host and
    every subdomain (``*.`` glob semantics, bare host included). ``regex``
    covers the remaining complex patterns.
    """

    exact_hosts: set[str] = field(default_factory=set)
    sub_hosts: set[str] = field(default_factory=set)
    regex: re.Pattern[str] | None = None


def _resolve_db_path() -> Path:
    configured = getattr(settings, "blocklist_sqlite_path", None) or getattr(
        settings, "blocklist_duckdb_path", ""
    )
    configured = configured.strip()
    return Path(configured) if configured else Path("data") / "blocklist.sqlite"


def _translate_ublacklist_to_regex(pattern: str) -> str:
    """Translate uBlacklist globs, including optional subdomains, to regex.

    The host portion ends at ``(?:[:/]|$)`` so bare hosts
    (``https://example.com``) and port URLs (``https://example.com:8443/a``)
    match, while suffix look-alikes (``example.com.evil.com``) do not.
    """
    pattern = pattern.strip()
    if not pattern:
        return r"(?!)"  # never match

    # Normalize scheme
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

    host_part, _, path_part = pattern.partition("/")
    host_parts = host_part.split("*")
    host_re = ".*".join(re.escape(p) for p in host_parts)
    boundary = r"(?:[:/]|$)"
    if path_part:
        path_parts = path_part.split("*")
        path_re = ".*".join(re.escape(p) for p in path_parts)
        return f"{scheme}{subdomain}{host_re}{boundary}{path_re}$"
    # No explicit path: match the bare host and any port/path.
    return f"{scheme}{subdomain}{host_re}{boundary}.*$"


def _glob_host(glob_pattern: str) -> tuple[str, bool] | None:
    """Return (canonical_host, subdomains_allowed) for pure-domain globs.

    Pure-domain globs are ``*://*.example.com/*``-style; they can be matched
    with set lookup instead of a regex alternation. Returns None for patterns
    that need regex (ports, partial paths, embedded wildcards).
    """
    p = glob_pattern.strip()
    for prefix in ("*://", "https://", "http://"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
            break
    sub = False
    if p.startswith("*."):
        sub = True
        p = p[2:]
    host, _, path = p.partition("/")
    if not host or "*" in host or ":" in host or "?" in host or " " in host:
        return None
    if path and path != "*" and not path.startswith("*"):
        return None
    host = host.strip(".").lower()
    if not host or not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    return host, sub


def _extract_host(url: str) -> str:
    try:
        host = urlsplit(url if "://" in url else f"//{url}").hostname
    except ValueError:
        return ""
    return (host or "").lower()


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


def _compile(db_path: Path) -> BlocklistIndex:
    con = sqlite3.connect(str(db_path), timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT glob_pattern FROM blocklist_patterns WHERE active = 1"
        ).fetchall()
    finally:
        con.close()

    index = BlocklistIndex()
    complex_patterns: list[str] = []
    for row in rows:
        glob_pat = row["glob_pattern"]
        host = _glob_host(glob_pat)
        if host is not None:
            canonical, sub = host
            (index.sub_hosts if sub else index.exact_hosts).add(canonical)
            continue
        complex_patterns.append(_translate_ublacklist_to_regex(glob_pat))
    if complex_patterns:
        index.regex = re.compile("|".join(complex_patterns), re.IGNORECASE)
    return index


def _get_blocklist_index() -> BlocklistIndex:
    global _index_cache
    if _index_cache is None:
        with _index_lock:
            if _index_cache is None:
                db_path = _resolve_db_path()
                _ensure_db(db_path)
                _index_cache = _compile(db_path)
    return _index_cache


def reload_blocklist() -> None:
    global _index_cache
    with _index_lock:
        _index_cache = None
    _get_blocklist_index()


def is_blocked_url(url: str) -> bool:
    if not url:
        return False
    index = _get_blocklist_index()
    host = _extract_host(url)
    if host:
        if host in index.exact_hosts or host in index.sub_hosts:
            return True
        # Suffix walk catches subdomains of blocked hosts: a.b.example.com
        # is blocked when example.com is in sub_hosts.
        dot = host.find(".")
        while dot != -1:
            if host[dot + 1 :] in index.sub_hosts:
                return True
            dot = host.find(".", dot + 1)
    if index.regex is not None and index.regex.match(url):
        return True
    return False


def filter_blocked_results(results: list[WebSearchResult]) -> list[WebSearchResult]:
    return [result for result in results if not is_blocked_url(result.link)]


def add_blocklist_pattern(glob_pattern: str, source: str = "manual") -> bool:
    glob_pattern = glob_pattern.strip()
    if not glob_pattern:
        return False
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


# ── Subscription import ──────────────────────────────────────────────────


def _fetch_subscription_text(url: str, timeout: float = 15.0, max_bytes: int = 100 * 1024 * 1024) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "web-search-mcp-blocklist-importer/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"subscription too large ({len(data)} bytes > {max_bytes})")
    return data.decode("utf-8", errors="replace")


def _normalize_subscription_line(line: str) -> str | None:
    """Normalize one subscription line to a uBlacklist glob, or None to skip.

    - uBlacklist match patterns (``*://...``) pass through.
    - Plain domains (SSSS ``domains.txt`` style) become ``*://*.domain/*``.
    - Regex literals, unblock rules, and comments are skipped (regex rules
      would reintroduce the backtracking risk).
    """
    line = line.split(" #", 1)[0].strip()
    if not line or line.startswith(("#", "!", "@")):
        return None
    if line.startswith("/") or "|" in line or "(" in line or ")" in line or "=" in line:
        return None
    if line.startswith(("*://", "http://", "https://")):
        return line
    if "." in line and not line.startswith(".") and re.fullmatch(r"[a-z0-9.-]+", line):
        return f"*://*.{line}/*"
    return None


def import_subscription(url: str, source: str = "subscription", timeout: float = 15.0) -> dict:
    """Fetch a community blocklist and merge it into the store.

    Accepts uBlacklist globs or plain domains (one per line, ``#`` comments
    allowed). Returns a stats dict; raises on network/size errors.
    """
    text = _fetch_subscription_text(url, timeout=timeout)
    candidates: list[str] = []
    skipped = 0
    for raw in text.splitlines():
        normalized = _normalize_subscription_line(raw)
        if normalized is None:
            skipped += 1
            continue
        candidates.append(normalized)

    path = _resolve_db_path()
    _ensure_db(path)
    payload = [
        (glob_pat, _translate_ublacklist_to_regex(glob_pat), source)
        for glob_pat in candidates
    ]
    with _db_lock:
        con = sqlite3.connect(str(path), timeout=10.0)
        try:
            with con:
                con.executemany(
                    """
                    INSERT INTO blocklist_patterns (glob_pattern, regex_pattern, source, active)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(glob_pattern) DO UPDATE SET
                        regex_pattern = excluded.regex_pattern,
                        source = excluded.source,
                        active = 1
                    """,
                    payload,
                )
            active_total = con.execute(
                "SELECT count(*) FROM blocklist_patterns WHERE active = 1"
            ).fetchone()[0]
        finally:
            con.close()
    reload_blocklist()
    return {
        "source": source,
        "candidates": len(candidates),
        "skipped": skipped,
        "active_total": active_total,
    }


def list_active_patterns(limit: int = 100) -> list[dict]:
    path = _resolve_db_path()
    _ensure_db(path)
    con = sqlite3.connect(str(path), timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT glob_pattern, source, created_at FROM blocklist_patterns "
            "WHERE active = 1 ORDER BY glob_pattern LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def blocklist_stats() -> dict:
    index = _get_blocklist_index()
    return {
        "exact_hosts": len(index.exact_hosts),
        "sub_hosts": len(index.sub_hosts),
        "complex_regex_patterns": 1 if index.regex is not None else 0,
    }
