"""Brave Search API provider."""
from __future__ import annotations

import os
from typing import Any

import httpx

from ..models import WebSearchResult
from ..retry import retry_with_backoff


class BraveError(RuntimeError):
    pass


class BraveConfigError(BraveError):
    pass


def _get_brave_api_key() -> str:
    api_key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not api_key:
        raise BraveConfigError(
            "BRAVE_API_KEY is not set. Configure it as an environment variable."
        )
    return api_key


async def search_brave(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Query Brave Search API and return parsed results.

    Brave endpoint:
    - GET https://api.search.brave.com/res/v1/web/search
    - Header: X-Subscription-Token: <BRAVE_API_KEY>

    Docs: https://brave.com/search/api/
    """
    if not query.strip():
        return []

    if num_results < 1:
        return []

    api_key = _get_brave_api_key()
    url = "https://api.search.brave.com/res/v1/web/search"
    params = {"q": query, "count": num_results}
    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise BraveError("Brave response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise BraveError("Brave response was not a JSON object.")
        return data

    if http_client is None:
        async with httpx.AsyncClient(timeout=30) as client:
            async def _request() -> dict[str, Any]:
                return await _do_request(client)
            data = await retry_with_backoff(
                _request,
                provider_name="brave",
                max_retries=2,
            )
    else:
        async def _request_with_client() -> dict[str, Any]:
            return await _do_request(http_client)
        data = await retry_with_backoff(
            _request_with_client,
            provider_name="brave",
            max_retries=2,
        )

    # Brave response structure: {"web": {"results": [...}}
    web_data = data.get("web", {})
    if not isinstance(web_data, dict):
        return []

    raw_results = web_data.get("results", [])
    if not isinstance(raw_results, list):
        return []

    results: list[WebSearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        link = item.get("url")
        snippet = item.get("description")
        if not isinstance(title, str) or not isinstance(link, str):
            continue
        if not isinstance(snippet, str):
            snippet = ""

        results.append(WebSearchResult(title=title, link=link, snippet=snippet))
        if len(results) >= num_results:
            break

    return results
