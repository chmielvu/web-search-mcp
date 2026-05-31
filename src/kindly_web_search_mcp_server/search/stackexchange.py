"""StackExchange search provider via public API v2.3.

No API key required for 300 requests/day.
Optional STACKEXCHANGE_APP_KEY increases quota to 10,000 requests/day.

API: GET https://api.stackexchange.com/2.3/search
IMPORTANT: Uses 'intitle=' parameter (the /search endpoint requires intitle or tagged).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..models import WebSearchResult

_SE_BASE = "https://api.stackexchange.com/2.3/search"
_SITES = "stackoverflow+serverfault+superuser+askubuntu"


async def search_stackexchange(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search StackExchange sites for relevant Q&A.

    Searches across Stack Overflow, Server Fault, Super User, and Ask Ubuntu.
    Uses intitle= for targeted title search.

    Args:
        query: Normalized search query string.
        num_results: Maximum number of results to return.
        http_client: Optional shared httpx client.

    Returns:
        List of WebSearchResult objects (empty on failure or quota exhausted).
    """
    if not query.strip() or num_results < 1:
        return []

    params: dict[str, Any] = {
        "order": "desc",
        "sort": "votes",
        "intitle": query,
        "site": _SITES,
        "pagesize": min(num_results, 50),
    }

    # Optional app key for higher quota
    app_key = os.environ.get("STACKEXCHANGE_APP_KEY", "").strip()
    if app_key:
        params["key"] = app_key

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.get(_SE_BASE, params=params)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    try:
        if http_client is not None:
            data = await _do_request(http_client)
        else:
            async with httpx.AsyncClient(timeout=15) as client:
                data = await _do_request(client)
    except Exception:
        return []

    # Check quota
    quota_remaining = data.get("quota_remaining")
    if quota_remaining is not None and quota_remaining <= 0:
        return []

    items = data.get("items")
    if not isinstance(items, list):
        return []

    results: list[WebSearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        title = item.get("title")
        link = item.get("link")
        score = item.get("score", 0)
        answer_count = item.get("answer_count", 0)
        tags = item.get("tags", [])

        if not isinstance(title, str) or not title:
            continue
        if not isinstance(link, str) or not link:
            continue

        tag_str = "; ".join(tags[:4]) if isinstance(tags, list) else ""
        snippet = f"Score: {score} | {answer_count} answers"
        if tag_str:
            snippet += f" | [{tag_str}]"

        results.append(WebSearchResult(title=title, link=link, snippet=snippet))
        if len(results) >= num_results:
            break

    return results
