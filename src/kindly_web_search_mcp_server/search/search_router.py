"""Search Router API provider — free general SERP provider."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..models import WebSearchResult
from ..retry import retry_with_backoff


class SearchRouterError(RuntimeError):
    pass


class SearchRouterConfigError(SearchRouterError):
    pass


def _get_search_router_api_key() -> str:
    api_key = os.environ.get("SEARCH_ROUTER_API_KEY", "").strip()
    if not api_key:
        raise SearchRouterConfigError(
            "SEARCH_ROUTER_API_KEY is not set. Configure it as an environment variable."
        )
    return api_key


async def search_search_router(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Query Search Router API and return parsed results.

    Endpoint:
    - POST https://search-router.com/api/search
    - Header: X-API-Key: <SEARCH_ROUTER_API_KEY>

    Docs: https://search-router.com/docs
    """
    if not query.strip():
        return []

    if num_results < 1:
        return []

    api_key = _get_search_router_api_key()
    url = "https://search-router.com/api/search"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
    body = {"query": query, "num_results": num_results}

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise SearchRouterError(
                "Search Router response was not valid JSON."
            ) from exc
        if not isinstance(data, dict):
            raise SearchRouterError("Search Router response was not a JSON object.")
        return data

    if http_client is None:
        async with httpx.AsyncClient(timeout=30) as client:

            async def _request() -> dict[str, Any]:
                return await _do_request(client)

            data = await retry_with_backoff(
                _request,
                provider_name="search_router",
                max_retries=2,
            )
    else:

        async def _request_with_client() -> dict[str, Any]:
            return await _do_request(http_client)

        data = await retry_with_backoff(
            _request_with_client,
            provider_name="search_router",
            max_retries=2,
        )

    raw_results = data.get("results", [])
    if not isinstance(raw_results, list):
        return []

    results: list[WebSearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        link = item.get("url")
        snippet = (
            item.get("snippet") or item.get("description") or item.get("content") or ""
        )
        domain = item.get("domain")
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(link, str)
            or not link.strip()
        ):
            continue
        if not isinstance(snippet, str):
            snippet = ""
        if not isinstance(domain, str):
            domain = None

        results.append(
            WebSearchResult(
                title=title,
                link=link,
                snippet=snippet,
                domain=domain,
            )
        )
        if len(results) >= num_results:
            break

    return results
