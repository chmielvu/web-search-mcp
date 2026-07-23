"""Serper (Google SERP) API provider."""

from __future__ import annotations

from typing import Any

import httpx

from ...models import WebSearchResult
from ...settings import get_env_value, settings
from ...utils.url_canonicalize import extract_domain_from_url
from .base import run_provider


class SerperError(RuntimeError):
    pass


class SerperConfigError(SerperError):
    pass


def _get_serper_api_key() -> str:
    api_key = get_env_value("SERPER_API_KEY", settings.serper_api_key).strip()
    if not api_key:
        raise SerperConfigError("SERPER_API_KEY is not set. Configure it in your runtime settings.")
    return api_key


async def search_serper(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Query Serper (Google SERP) API and return parsed results.

    Serper endpoint:
    - POST https://google.serper.dev/search
    - Header: X-API-KEY: <SERPER_API_KEY>
    - Body: {"q": query, "num": num_results}

    Docs: https://serper.dev/
    """
    api_key = _get_serper_api_key()
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    bounded_num = max(1, min(num_results, 10))
    clean_query = query.strip()
    body = {"q": clean_query, "num": bounded_num}

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(url, json=body, headers=headers)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise SerperError("Serper response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise SerperError("Serper response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        organic = data.get("organic", [])
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

            domain = extract_domain_from_url(link)
            results.append(
                WebSearchResult(
                    title=title.strip(),
                    link=link.strip(),
                    snippet=snippet.strip(),
                    domain=domain,
                )
            )
            if len(results) >= num_results:
                break
        return results

    return await run_provider(
        "serper",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
    )
