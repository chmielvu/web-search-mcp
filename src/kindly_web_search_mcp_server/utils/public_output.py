"""Public web_search envelope and leftover-overflow cursor codec.

Snippet normalization lives here too (merged from ``snippet_normalizer.py``,
its only consumer) so the public-output surface is one module.
"""

from __future__ import annotations

import base64
import json
import re
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

_OVERFLOW_CURSOR_VERSION = 2
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


def encode_web_search_overflow_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def decode_web_search_overflow_cursor(cursor: str) -> dict[str, Any]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
    except Exception as exc:
        raise ValueError(
            "Invalid web_search cursor. Call web_search without cursor to start a new search, "
            "then pass response.cursor to page leftover links from that run."
        ) from exc
    if not isinstance(decoded, dict):
        raise ValueError(
            "Invalid web_search cursor. Call web_search without cursor to start a new search."
        )
    if decoded.get("v") != _OVERFLOW_CURSOR_VERSION or decoded.get("kind") != "web_search_overflow":
        raise ValueError(
            "Unsupported web_search cursor version. Call web_search without cursor to start a new search."
        )
    if "items" not in decoded or not isinstance(decoded["items"], list):
        raise ValueError(
            "Invalid web_search cursor. Call web_search without cursor to start a new search."
        )
    return decoded


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


def to_public_web_search(
    *,
    query: str,
    hits: list[ScoredHit],
    overflow_items: list[tuple[str, SearchHit]],
    warnings: list[ProviderWarning] | None,
    synthesis: str | None,
    search: WebSearchExecution,
) -> WebSearchPublicResponse:
    public_hits: list[WebSearchHit | WebSearchOverflowHit] = []
    for index, result in enumerate(hits, start=1):
        if not result.hit.url:
            continue
        public_hits.append(to_public_hit(result, f"c{index}"))

    overflow = _compact_overflow(overflow_items)
    cursor: str | None = None
    remaining: int | None = None
    if overflow:
        remaining = len(overflow)
        cursor = encode_web_search_overflow_cursor(
            {
                "v": _OVERFLOW_CURSOR_VERSION,
                "kind": "web_search_overflow",
                "query": query,
                "citation_base": len(public_hits),
                "items": overflow,
                "warnings": [w.model_dump(exclude_none=True) for w in warnings] if warnings else [],
            }
        )

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
    )


def page_overflow_cursor(decoded: dict[str, Any]) -> WebSearchPublicResponse:
    items = decoded["items"]
    page = items[:FINAL_RESULT_LIMIT]
    rest = items[FINAL_RESULT_LIMIT:]
    if not page:
        raise ValueError(
            "web_search cursor has no remaining results. Call web_search without cursor to start a new search."
        )
    citation_base = int(decoded.get("citation_base") or 0)
    overflow_hits: list[WebSearchHit | WebSearchOverflowHit] = []
    for index, item in enumerate(page, start=1):
        title = item.get("title") if isinstance(item, dict) else None
        url = item.get("url") if isinstance(item, dict) else None
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
    query = str(decoded.get("query") or "")
    rebuilt_warnings = [
        ProviderWarning.model_validate(item)
        for item in (decoded.get("warnings") or [])
        if isinstance(item, dict)
    ]
    cursor = None
    remaining = None
    if rest:
        remaining = len(rest)
        cursor = encode_web_search_overflow_cursor(
            {
                "v": _OVERFLOW_CURSOR_VERSION,
                "kind": "web_search_overflow",
                "query": query,
                "citation_base": citation_base + len(page),
                "items": rest,
                "warnings": [w.model_dump(exclude_none=True) for w in rebuilt_warnings]
                if rebuilt_warnings
                else [],
            }
        )
    return WebSearchPublicResponse(
        query=query,
        results=overflow_hits,
        warnings=rebuilt_warnings or None,
        next=fetch_next(
            [hit.url for hit in overflow_hits],
            why=_OVERFLOW_FETCH_WHY,
            confidence="medium",
            limit=_FETCH_NEXT_LIMIT,
        ),
        remaining=remaining,
        cursor=cursor,
    )
