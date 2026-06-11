"""Brave Search API provider."""

from __future__ import annotations

from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import get_env_value, settings
from .base_provider import run_provider


class BraveError(RuntimeError):
    pass


class BraveConfigError(BraveError):
    pass


def _get_brave_api_key() -> str:
    api_key = get_env_value("BRAVE_API_KEY", settings.brave_api_key).strip()
    if not api_key:
        raise BraveConfigError(
            "BRAVE_API_KEY is not set. Configure it in your runtime settings."
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
    api_key = _get_brave_api_key()
    url = "https://api.search.brave.com/res/v1/web/search"
    params = {"q": query, "count": num_results}
    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise BraveError("Brave response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise BraveError("Brave response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
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
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(link, str)
                or not link.strip()
            ):
                continue
            if not isinstance(snippet, str):
                snippet = ""

            results.append(WebSearchResult(title=title, link=link, snippet=snippet))
            if len(results) >= num_results:
                break
        return results

    return await run_provider(
        "brave",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
    )
