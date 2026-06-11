from __future__ import annotations

from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import get_env_value, settings
from .base_provider import run_provider


class TavilyError(RuntimeError):
    pass


class TavilyConfigError(TavilyError):
    pass


def _get_tavily_api_key() -> str:
    api_key = get_env_value("TAVILY_API_KEY", settings.tavily_api_key).strip()
    if not api_key:
        raise TavilyConfigError(
            "TAVILY_API_KEY is not set. Configure it in your runtime settings."
        )
    return api_key


async def search_tavily(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """
    Query Tavily Search API and return parsed results.

    Tavily endpoint:
    - POST https://api.tavily.com/search
    - Header: Authorization: Bearer <TAVILY_API_KEY>
    - JSON: {"query": "<query>", "max_results": <num_results>, "search_depth": "basic", ...}

    Docs: https://docs.tavily.com/documentation/api-reference/endpoint/search
    """
    api_key = _get_tavily_api_key()
    url = "https://api.tavily.com/search"
    payload = {
        "query": query,
        "max_results": int(num_results),
        "search_depth": "basic",
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise TavilyError("Tavily response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise TavilyError("Tavily response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise TavilyError("Tavily response missing `results` list.")

        results: list[WebSearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            link = item.get("url")
            snippet = item.get("content")
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(link, str)
                or not link.strip()
                or not isinstance(snippet, str)
            ):
                continue

            results.append(WebSearchResult(title=title, link=link, snippet=snippet))
            if len(results) >= num_results:
                break
        return results

    return await run_provider(
        "tavily",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
    )
