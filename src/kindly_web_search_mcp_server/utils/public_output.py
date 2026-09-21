"""Public web_search envelope and leftover-overflow cursor codec.

Snippet normalization lives here too (merged from ``snippet_normalizer.py``,
its only consumer) so the public-output surface is one module.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

from ..models import (
    ProviderWarning,
    WebSearchExecution,
    WebSearchHit,
    WebSearchOverflowHit,
    WebSearchPublicResponse,
    fetch_next,
)
from ..rerank.models import FINAL_RESULT_LIMIT
from ..search.contracts import SearchRun
from ..search.ranking import _build_freshness_signal
from ..search.types import ScoredHit, SearchHit
from .text_clean import clean_text_for_llm

_PAGE_TTL_SECONDS = 24 * 60 * 60
_FETCH_NEXT_LIMIT = 5

# --- Snippet normalization (merged from snippet_normalizer.py) ---

MAX_SNIPPET_LENGTH = 500

# Patterns to strip entirely. Fixes from the 2026-09-09 audit:
# - HTML-tag regex requires a tag-shaped payload (letter after optional /):
#   the old ``<[^>]+>`` ate math prose like "if a < b and c > d" (verified).
# - Navigation chrome is stripped only when it is a WHOLE LINE or a bracketed
#   token: the old unanchored inline pattern also matched the common word
#   "join" mid-prose ("How to join two tables in SQL" -> "How to two tables",
#   verified twice — word-boundary anchoring alone is insufficient because a
#   bare word has whitespace on both sides).
_SNIPPET_STRIP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"</?[a-zA-Z][^>]*>", re.DOTALL),
    re.compile(r"data:[a-zA-Z/+]+;base64,[A-Za-z0-9+/=]{50,}"),
    re.compile(
        r"^\s*\[?\s*(?:Sign\s*Up|Log\s*In|Join|Subscribe|Download)\s*\]?\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"\[\s*(?:Sign\s*Up|Log\s*In|Join|Subscribe|Download)\s*\]",
        re.IGNORECASE,
    ),
    re.compile(r"https?://\S{80,}"),
)
_SNIPPET_MULTI_WHITESPACE = re.compile(r"[ \t]+")
_SNIPPET_MULTI_NEWLINES = re.compile(r"\n{3,}")


def normalize_snippet(text: str, *, max_length: int = MAX_SNIPPET_LENGTH) -> str:
    """Clean a raw snippet for MCP tool output.

    1. Strip HTML tags and base64 data URIs
    2. Remove navigation chrome (Sign Up, Log In, etc.)
    3. Collapse whitespace
    4. Truncate to *max_length* with ellipsis
    """
    if not text:
        return ""

    cleaned = clean_text_for_llm(text, role="snippet")
    for pattern in _SNIPPET_STRIP_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)

    cleaned = _SNIPPET_MULTI_WHITESPACE.sub(" ", cleaned)
    cleaned = _SNIPPET_MULTI_NEWLINES.sub("\n\n", cleaned)
    cleaned = cleaned.strip()

    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1].rstrip() + "…"

    return cleaned


_PAGE1_FETCH_WHY = (
    "Evaluate these results first. Then call fetch on the URLs you need; "
    "snippets are not page text."
)
_OVERFLOW_FETCH_WHY = (
    "These leftover links were ranked below the top 15. Fetch only if you still need more sources."
)


def encode_cursor(offset: int) -> str:
    """Encode a page offset as an opaque base64url cursor (PraisonAI/FastMCP shape).

    The cursor carries position only — never result data. Leftover items live
    server-side in the page store keyed by run; the caller pairs this token
    with the run it came from.
    """
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    """Decode an offset cursor; raise ValueError when malformed or tampered with."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        offset = int(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception as exc:
        raise ValueError(
            "Invalid web_search cursor. Call web_search without cursor to start a new search, "
            "then pass response.cursor to page leftover links from that run."
        ) from exc
    if isinstance(offset, bool) or offset < 0:
        raise ValueError(
            "Invalid web_search cursor. Call web_search without cursor to start a new search, "
            "then pass response.cursor to page leftover links from that run."
        )
    return offset


def to_public_hit(result: ScoredHit, citation_id: str) -> WebSearchHit:
    providers = list(result.providers) if result.providers else None
    return WebSearchHit(
        citation_id=citation_id,
        title=result.hit.title,
        url=result.hit.url,
        snippet=normalize_snippet(result.hit.snippet),
        domain=result.hit.domain,
        published_date=result.hit.published,
        freshness=_build_freshness_signal(result.hit.published),
        consensus=len(providers) if providers is not None else None,
        providers=providers if providers else None,
    )


def _compact_overflow(
    overflow_items: list[tuple[str, SearchHit]],
) -> list[dict[str, str]]:
    compact: list[dict[str, str]] = []
    for item in (row[1] for row in overflow_items):
        if not item.url:
            continue
        compact.append({"title": item.title, "url": item.url})
    return compact


def _pages_connection(write: bool = False) -> Any:
    """Open jobs.sqlite with the shared WAL/busy-timeout discipline."""
    import sqlite3

    from ..cli.services.jobs import jobs_db_path

    path = jobs_db_path()
    uri = f"file:{path}" if write else f"file:{path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    if write:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS search_pages (
                run_key TEXT PRIMARY KEY,
                query TEXT NOT NULL DEFAULT '',
                items_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_search_pages_expires ON search_pages(expires_at);
            """
        )
    return connection


def store_overflow_page(
    run_key: str,
    query: str,
    overflow: list[dict[str, str]],
    warnings: list[ProviderWarning] | None,
) -> None:
    """Persist one run's leftover list for offset-cursor paging (24h TTL)."""
    now = int(time.time())
    connection = _pages_connection(write=True)
    try:
        connection.execute("DELETE FROM search_pages WHERE expires_at <= ?", (now,))
        connection.execute(
            "INSERT INTO search_pages (run_key, query, items_json, warnings_json, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(run_key) DO UPDATE SET query=excluded.query,"
            " items_json=excluded.items_json, warnings_json=excluded.warnings_json,"
            " created_at=excluded.created_at, expires_at=excluded.expires_at",
            (
                run_key,
                query,
                json.dumps(overflow, ensure_ascii=False, separators=(",", ":")),
                json.dumps(
                    [w.model_dump(exclude_none=True) for w in warnings] if warnings else [],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                now,
                now + _PAGE_TTL_SECONDS,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def load_overflow_page(run_key: str) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
    """Load a run's leftover list; ValueError when expired or never stored."""
    connection = _pages_connection()
    try:
        row = connection.execute(
            "SELECT query, items_json, warnings_json FROM search_pages WHERE run_key = ?",
            (run_key,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(
            "web_search cursor expired: the run is no longer retained. "
            "Call web_search without cursor to start a new search."
        )
    try:
        items = json.loads(row["items_json"])
        warnings = json.loads(row["warnings_json"])
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "web_search cursor expired: the run is no longer retained. "
            "Call web_search without cursor to start a new search."
        ) from exc
    if not isinstance(items, list):
        raise ValueError(
            "web_search cursor expired: the run is no longer retained. "
            "Call web_search without cursor to start a new search."
        )
    clean = [
        {"title": item["title"], "url": item["url"]}
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("title"), str)
        and isinstance(item.get("url"), str)
        and item["url"]
    ]
    return str(row["query"] or ""), clean, warnings if isinstance(warnings, list) else []


def to_public_web_search(
    *,
    query: str,
    hits: list[ScoredHit],
    overflow_items: list[tuple[str, SearchHit]],
    warnings: list[ProviderWarning] | None,
    synthesis: str | None,
    search: WebSearchExecution,
    run_key: str | None = None,
) -> WebSearchPublicResponse:
    public_hits: list[WebSearchHit | WebSearchOverflowHit] = []
    for index, result in enumerate(hits, start=1):
        if not result.hit.url:
            continue
        public_hits.append(to_public_hit(result, f"c{index}"))

    overflow = _compact_overflow(overflow_items)
    cursor: str | None = None
    remaining: int | None = None
    # Server-side paging (PraisonAI/FastMCP shape): leftovers persist under
    # the run key; the token is just the base64 offset of the next page.
    if overflow and run_key:
        remaining = len(overflow)
        store_overflow_page(run_key, query, overflow, warnings)
        cursor = encode_cursor(len(public_hits))

    return WebSearchPublicResponse(
        query=query,
        results=public_hits,
        synthesis=synthesis,
        search=search,
        warnings=warnings if warnings else None,
        next=fetch_next(
            [hit.url for hit in public_hits],
            why=_PAGE1_FETCH_WHY,
            confidence="high",
            limit=_FETCH_NEXT_LIMIT,
        ),
        remaining=remaining,
        cursor=cursor,
    )


def to_public_web_search_from_run(run: SearchRun) -> WebSearchPublicResponse:
    response = run.response
    if response is None:
        raise ValueError("Search run has no response")
    if response.rounds < 1 or response.stop_reason is None:
        raise ValueError("Search run result is missing adaptive execution metadata")
    return to_public_web_search(
        query=response.query,
        hits=list(response.hits),
        overflow_items=list(run.diagnostics.overflow_ranked),
        warnings=list(response.warnings) if response.warnings else None,
        synthesis=response.synthesis,
        search=WebSearchExecution(rounds=response.rounds, stop_reason=response.stop_reason),
        run_key=run.run_key,
    )


def page_overflow_cursor(run_key: str, cursor: str) -> WebSearchPublicResponse:
    """Page leftover links for ``run_key`` at the given offset cursor."""
    offset = decode_cursor(cursor)
    stored_query, stored_items, stored_warnings = load_overflow_page(run_key)
    if offset >= len(stored_items):
        raise ValueError(
            "Invalid web_search cursor. Call web_search without cursor to start a new search, "
            "then pass response.cursor to page leftover links from that run."
        )
    items = stored_items[offset:]
    page = items[:FINAL_RESULT_LIMIT]
    rest = items[FINAL_RESULT_LIMIT:]
    if not page:
        raise ValueError(
            "web_search cursor has no remaining results. Call web_search without cursor to start a new search."
        )
    citation_base = offset
    overflow_hits: list[WebSearchHit | WebSearchOverflowHit] = []
    for index, item in enumerate(page, start=1):
        title = item.get("title")
        url = item.get("url")
        if not isinstance(title, str) or not isinstance(url, str) or not url:
            continue
        overflow_hits.append(
            WebSearchOverflowHit(
                citation_id=f"c{citation_base + index}",
                title=title,
                url=url,
            )
        )
    if not overflow_hits:
        raise ValueError(
            "web_search cursor has no remaining results. Call web_search without cursor to start a new search."
        )
    rebuilt_warnings = [
        ProviderWarning.model_validate(item) for item in stored_warnings if isinstance(item, dict)
    ]
    next_cursor = None
    remaining = None
    if rest:
        remaining = len(rest)
        next_cursor = encode_cursor(offset + len(page))
    return WebSearchPublicResponse(
        query=stored_query,
        results=overflow_hits,
        warnings=rebuilt_warnings or None,
        next=fetch_next(
            [hit.url for hit in overflow_hits],
            why=_OVERFLOW_FETCH_WHY,
            confidence="medium",
            limit=_FETCH_NEXT_LIMIT,
        ),
        remaining=remaining,
        cursor=next_cursor,
    )
