"""Reddit search provider via public JSON API.

No API key required — uses User-Agent header for identification.
Rate limit: ~100 requests per ~4-minute window (add 2s delay between calls).

API: GET https://www.reddit.com/r/{subreddits}/search.json
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..models import WebSearchResult

_REDDIT_BASE = "https://www.reddit.com/r/programming+MachineLearning+LocalLLaMA+Rag+Python/search.json"
_USER_AGENT = "kindly-web-search-mcp/1.0 (research bot)"


async def search_reddit(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search Reddit across relevant technical subreddits.

    Args:
        query: Normalized search query string.
        num_results: Maximum number of results to return.
        http_client: Optional shared httpx client.

    Returns:
        List of WebSearchResult objects (empty on failure).
    """
    if not query.strip() or num_results < 1:
        return []

    # Reddit rate-limits aggressively — be polite
    await asyncio.sleep(2)

    params: dict[str, Any] = {
        "q": query,
        "limit": num_results,
        "sort": "relevance",
        "restrict_sr": "on",
    }
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.get(_REDDIT_BASE, params=params, headers=headers)
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
            async with httpx.AsyncClient(timeout=20) as client:
                data = await _do_request(client)
    except Exception:
        return []

    outer_data = data.get("data")
    if not isinstance(outer_data, dict):
        return []

    children = outer_data.get("children", [])
    if not isinstance(children, list):
        return []

    results: list[WebSearchResult] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        child_data = child.get("data")
        if not isinstance(child_data, dict):
            continue

        title = child_data.get("title")
        if not isinstance(title, str) or not title:
            continue

        # Prefer the overridden destination URL (canonical), fall back to url
        link = child_data.get("url_overridden_by_dest")
        if not isinstance(link, str) or not link:
            link = child_data.get("url")
        if not isinstance(link, str) or not link:
            continue

        subreddit = child_data.get("subreddit", "unknown")
        score = child_data.get("score", 0)
        num_comments = child_data.get("num_comments", 0)

        snippet = f"r/{subreddit} | {score} pts | {num_comments} comments"

        results.append(WebSearchResult(title=title, link=link, snippet=snippet))
        if len(results) >= num_results:
            break

    return results
