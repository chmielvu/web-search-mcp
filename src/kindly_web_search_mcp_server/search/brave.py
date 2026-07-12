"""Brave Search, Autosuggest, and Spellcheck API helpers."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import get_env_value, settings
from .base_provider import run_provider
from .brave_common import (
    BraveError,
    _get_brave_api_key,
    _brave_headers,
    _bound_brave_query,
    translate_brave_freshness,
    BRAVE_LLM_CONTEXT_URL,
)
from .normalize import normalize_query


async def suggest_brave_queries(
    query: str,
    *,
    count: int = 8,
    country: str = "US",
    lang: str = "en",
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Return Brave Autosuggest queries and rich entity metadata."""
    api_key = os.environ.get("BRAVE_SUGGEST_API_KEY", settings.brave_suggest_api_key).strip()
    if not api_key:
        return {"suggestions": [], "entities": []}

    url = "https://api.search.brave.com/res/v1/suggest/search"
    bounded_count = max(1, min(count, 20))
    params = {
        "q": query[:200],
        "count": bounded_count,
        "country": country,
        "lang": lang,
        "rich": "true",
    }
    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }

    async def _with_client(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return {"suggestions": [], "entities": []}
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            return {"suggestions": [], "entities": []}
        suggestions: list[str] = []
        entities: list[dict[str, str]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            suggestion = item.get("query")
            if isinstance(suggestion, str) and suggestion.strip():
                suggestions.append(normalize_query(suggestion))
            if item.get("is_entity") is True:
                title = item.get("title")
                description = item.get("description")
                if isinstance(title, str) and title.strip():
                    entities.append(
                        {
                            "name": title.strip(),
                            "description": description.strip()
                            if isinstance(description, str)
                            else "",
                        }
                    )
        return {
            "suggestions": list(dict.fromkeys(suggestions))[:bounded_count],
            "entities": entities,
        }

    if http_client is not None:
        return await _with_client(http_client)
    async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
        return await _with_client(client)


async def spellcheck_brave(
    query: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Return Brave's corrected query using the dedicated spellcheck API key.

    The dedicated ``BRAVE_SPELLCHECK_API_KEY`` is required; ``BRAVE_API_KEY``
    is for LLM Context/News and is NOT a fallback here.
    """
    api_key = get_env_value("BRAVE_SPELLCHECK_API_KEY", settings.brave_spellcheck_api_key).strip()
    if not api_key:
        return None
    url = "https://api.search.brave.com/res/v1/spellcheck"
    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }

    async def _with_client(client: httpx.AsyncClient) -> str | None:
        response = await client.get(url, params={"q": query}, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return None
        data = response.json()
        correction = data.get("correction") if isinstance(data, dict) else None
        return correction.strip() if isinstance(correction, str) and correction.strip() else None

    if http_client is not None:
        return await _with_client(http_client)
    async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
        return await _with_client(client)


async def search_brave(
    query: str,
    *,
    num_results: int,
    freshness: str | None = None,
    country: str | None = None,
    search_lang: str | None = None,
    goggles: list[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Query Brave LLM Context API and return parsed grounding results.

    Replaces the previous standard Brave Web search with the LLM-optimized
    ``/res/v1/llm/context`` endpoint. Parses ``grounding.generic`` into
    ``WebSearchResult`` (title/link/snippet) and never synthesizes an answer.
    """
    api_key = _get_brave_api_key()
    params: dict[str, Any] = {
        "q": _bound_brave_query(query),
        "count": min(num_results, 20),
        "maximum_number_of_urls": min(num_results, 50),
    }
    if country:
        params["country"] = country
    if search_lang:
        params["search_lang"] = search_lang
    brave_freshness = translate_brave_freshness(freshness)
    if brave_freshness:
        params["freshness"] = brave_freshness
    if goggles:
        params["goggles"] = list(goggles)

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(
            BRAVE_LLM_CONTEXT_URL, params=params, headers=_brave_headers(api_key)
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise BraveError("Brave LLM Context response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise BraveError("Brave LLM Context response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        grounding = data.get("grounding") if isinstance(data, dict) else None
        generic = grounding.get("generic") if isinstance(grounding, dict) else None
        if not isinstance(generic, list):
            return []
        results: list[WebSearchResult] = []
        for entry in generic:
            if not isinstance(entry, dict):
                continue
            link = entry.get("source")
            if not isinstance(link, str) or not link.strip():
                continue
            link = link.strip()
            title = entry.get("title")
            if not isinstance(title, str) or not title.strip():
                title = link.split("//")[-1].split("/")[0] or link
            snippet = entry.get("snippet") or entry.get("content") or entry.get("description") or ""
            if not isinstance(snippet, str):
                snippet = ""
            results.append(WebSearchResult(title=title, link=link, snippet=snippet.strip()))
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
