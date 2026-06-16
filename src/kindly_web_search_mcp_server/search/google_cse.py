"""Google Custom Search JSON API provider."""

from __future__ import annotations

from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import settings
from .base_provider import run_provider
from .options import SearchOptions
from .google_cse_quota import get_google_cse_quota_tracker


class GoogleCseError(RuntimeError):
    pass


class GoogleCseConfigError(GoogleCseError):
    pass


def _get_google_cse_credentials() -> tuple[str, str]:
    api_key = settings.google_cse_api_key.strip()
    engine_id = settings.google_cse_engine_id.strip()
    if not api_key:
        raise GoogleCseConfigError(
            "GOOGLE_API_KEY is not set. Configure it as an environment variable."
        )
    if not engine_id:
        raise GoogleCseConfigError(
            "GOOGLE_CSE_ENGINE_ID is not set. Configure it as an environment variable."
        )
    return api_key, engine_id


async def search_google_cse(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    search_options: SearchOptions | None = None,
) -> list[WebSearchResult]:
    if not query.strip() or num_results < 1:
        return []

    api_key, engine_id = _get_google_cse_credentials()
    url = "https://www.googleapis.com/customsearch/v1"
    params: dict[str, Any] = {
        "key": api_key,
        "cx": engine_id,
        "q": query,
        "num": min(max(num_results, 1), 10),
    }

    if search_options and search_options.domain_filters:
        params["siteSearch"] = search_options.domain_filters[0]
        params["siteSearchFilter"] = "i"
    elif search_options and search_options.site_filters:
        params["siteSearch"] = search_options.site_filters[0]
        params["siteSearchFilter"] = "i"

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(url, params=params)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise GoogleCseError("Google CSE response was not a JSON object.")
        return body

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            return []

        results: list[WebSearchResult] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            link = item.get("link")
            snippet = item.get("snippet") or item.get("htmlSnippet") or ""
            domain = item.get("displayLink")
            if not isinstance(title, str) or not title.strip():
                continue
            if not isinstance(link, str) or not link.strip():
                continue
            if not isinstance(snippet, str):
                snippet = ""
            results.append(
                WebSearchResult(
                    title=title,
                    link=link,
                    snippet=snippet,
                    domain=domain if isinstance(domain, str) and domain.strip() else None,
                )
            )
            if len(results) >= num_results:
                break

        return results

    try:
        results = await run_provider(
            "google_cse",
            query,
            num_results,
            request=_do_request,
            parse_response=_parse_response,
            http_client=http_client,
            timeout_seconds=settings.google_cse_timeout_seconds,
        )
    except Exception:
        get_google_cse_quota_tracker().record_call(success=False)
        raise

    get_google_cse_quota_tracker().record_call(success=True)
    return results
