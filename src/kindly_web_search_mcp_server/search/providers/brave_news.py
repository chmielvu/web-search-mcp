"""Brave News Search provider for the ``news`` intent.

Calls the Brave News endpoint (``/res/v1/news/search``) with the standard
Brave API key and parses headline/article results, mapping ISO ``page_age``
to ``WebSearchResult.published_date``.
"""

from __future__ import annotations

from typing import Any

import httpx

from ...models import WebSearchResult
from .base import run_provider
from .brave_common import (
    BraveError,
    _get_brave_api_key,
    _brave_headers,
    _bound_brave_query,
    translate_brave_freshness,
    BRAVE_NEWS_URL,
)


async def search_brave_news(
    query: str,
    *,
    num_results: int,
    freshness: str | None = None,
    country: str | None = None,
    search_lang: str | None = None,
    goggles: list[str] | None = None,
    safesearch: str | None = None,
    extra_snippets: bool = False,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Query Brave News and return parsed results.

    Uses the same standard Brave credentials/headers/query bound as the other
    Brave surfaces. Freshness maps through the shared translator; ``page_age``
    becomes ``published_date``.
    """
    if not query.strip() or num_results < 1:
        return []

    api_key = _get_brave_api_key()
    params: dict[str, Any] = {
        "q": _bound_brave_query(query),
        "count": max(1, min(num_results, 50)),
    }
    if country:
        params["country"] = country
    if search_lang:
        params["search_lang"] = search_lang
    brave_freshness = translate_brave_freshness(freshness)
    if brave_freshness:
        params["freshness"] = brave_freshness
    if safesearch:
        params["safesearch"] = safesearch
    if extra_snippets:
        params["extra_snippets"] = "true"
    if goggles:
        params["goggles"] = list(goggles)

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(BRAVE_NEWS_URL, params=params, headers=_brave_headers(api_key))
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise BraveError("Brave News response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise BraveError("Brave News response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            return []
        results: list[WebSearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in (None, "news_result"):
                continue
            title = item.get("title")
            link = item.get("url")
            if not isinstance(title, str) or not title.strip():
                continue
            if not isinstance(link, str) or not link.strip():
                continue
            snippet = item.get("description") or ""
            if not isinstance(snippet, str):
                snippet = ""
            result = WebSearchResult(
                title=title.strip(),
                link=link.strip(),
                snippet=snippet.strip(),
            )
            published = item.get("page_age")
            if isinstance(published, str) and published.strip():
                result = result.model_copy(update={"published_date": published.strip()})
            if extra_snippets:
                extras = item.get("extra_snippets")
                if isinstance(extras, list):
                    joined = " ".join(
                        str(s) for s in extras if isinstance(s, str) and s.strip()
                    ).strip()
                    if joined:
                        result = result.model_copy(
                            update={"snippet": (snippet.strip() + " " + joined).strip()}
                        )
            results.append(result)
            if len(results) >= num_results:
                break
        return results

    return await run_provider(
        "brave_news",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
    )
