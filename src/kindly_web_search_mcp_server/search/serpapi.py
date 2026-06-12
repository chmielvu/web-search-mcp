"""SerpApi (Google SERP) API provider."""

from __future__ import annotations

from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import get_env_value, settings
from .base_provider import run_provider


class SerpApiError(RuntimeError):
    pass


class SerpApiConfigError(SerpApiError):
    pass


def _get_serpapi_api_key() -> str:
    api_key = get_env_value("SERPAPI_API_KEY", settings.serpapi_api_key).strip()
    if not api_key:
        raise SerpApiConfigError(
            "SERPAPI_API_KEY is not set. Configure it in your runtime settings."
        )
    return api_key


async def search_serpapi(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Query SerpApi and return parsed results.

    SerpApi endpoint:
    - GET https://serpapi.com/search
    - Params: q=query, num=num_results, api_key=<SERPAPI_API_KEY>, engine=<default_engine>

    Docs: https://serpapi.com/
    """
    api_key = _get_serpapi_api_key()
    url = "https://serpapi.com/search"
    params = {
        "q": query,
        "num": num_results,
        "api_key": api_key,
        "engine": settings.serpapi_default_engine,
    }

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(url, params=params)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise SerpApiError("SerpApi response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise SerpApiError("SerpApi response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        organic = data.get("organic_results", [])
        if not isinstance(organic, list):
            return []

        results: list[WebSearchResult] = []
        for item in organic:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            link = item.get("link")
            snippet = item.get("snippet")
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
        "serpapi",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
    )