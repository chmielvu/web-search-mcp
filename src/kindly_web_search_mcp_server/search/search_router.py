"""Search Router API provider — free general SERP provider."""

from __future__ import annotations

from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import get_env_value, settings
from .base_provider import run_provider


class SearchRouterError(RuntimeError):
    pass


class SearchRouterConfigError(SearchRouterError):
    pass


def _get_search_router_api_key() -> str:
    api_key = get_env_value(
        "SEARCH_ROUTER_API_KEY",
        settings.search_router_api_key,
    ).strip()
    if not api_key:
        raise SearchRouterConfigError(
            "SEARCH_ROUTER_API_KEY is not set. Configure it in your runtime settings."
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
    api_key = _get_search_router_api_key()
    url = "https://search-router.com/api/search"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
    body = {"query": query, "num_results": num_results}

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise SearchRouterError(
                "Search Router response was not valid JSON."
            ) from exc
        if not isinstance(data, dict):
            raise SearchRouterError("Search Router response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
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
                item.get("snippet")
                or item.get("description")
                or item.get("content")
                or ""
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

    return await run_provider(
        "search_router",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
    )
