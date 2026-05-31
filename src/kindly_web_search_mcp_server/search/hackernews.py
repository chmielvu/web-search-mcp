"""HackerNews search provider via Algolia Search API.

No API key required. Rate limit: 10,000 requests/hour.
API docs: https://hn.algolia.com/api
"""

from __future__ import annotations

from typing import Any

import httpx

from ..models import WebSearchResult

_HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"


async def search_hackernews(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search HackerNews stories via Algolia API.

    Args:
        query: Normalized search query string.
        num_results: Maximum number of results to return.
        http_client: Optional shared httpx client.

    Returns:
        List of WebSearchResult objects (empty on failure).
    """
    if not query.strip() or num_results < 1:
        return []

    params: dict[str, Any] = {
        "query": query,
        "tags": "story",
        "hitsPerPage": num_results,
    }

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.get(_HN_SEARCH_URL, params=params)
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

    hits = data.get("hits")
    if not isinstance(hits, list):
        return []

    results: list[WebSearchResult] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue

        title = hit.get("title")
        url = hit.get("url")
        object_id = hit.get("objectID")
        points = hit.get("points", 0)
        num_comments = hit.get("num_comments", 0)
        created = hit.get("created_at", "")

        if not isinstance(title, str) or not title:
            continue

        # Self-posts have no url — fall back to HN item page
        link: str
        if isinstance(url, str) and url:
            link = url
        elif isinstance(object_id, str) and object_id:
            link = f"https://news.ycombinator.com/item?id={object_id}"
        else:
            continue

        snippet = f"{points} pts | {num_comments} comments"
        if isinstance(created, str) and created:
            snippet += f" | {created[:10]}"

        results.append(WebSearchResult(title=title, link=link, snippet=snippet))
        if len(results) >= num_results:
            break

    return results
