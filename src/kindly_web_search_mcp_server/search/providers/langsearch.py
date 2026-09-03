"""LangSearch Web Search API provider.

Docs: https://docs.langsearch.com/api/web-search-api
Endpoint: POST https://api.langsearch.com/v1/web-search
Auth: Bearer token
Response: Bing-compatible JSON under data.webPages.value
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from ...models import WebSearchResult
from ...settings import get_env_value, settings
from ..filters import langsearch_freshness
from ..options import SearchOptions
from .base import ProviderRequestError, run_provider

logger = logging.getLogger(__name__)


class LangSearchError(ProviderRequestError):
    pass


class LangSearchConfigError(LangSearchError):
    pass


def _get_langsearch_api_key() -> str:
    api_key = get_env_value("LANGSEARCH_API_KEY", settings.langsearch_api_key).strip()
    if not api_key:
        raise LangSearchConfigError(
            "LANGSEARCH_API_KEY is not set. Configure it in your runtime settings."
        )
    return api_key


async def search_langsearch(
    query: str,
    *,
    num_results: int,
    search_options: SearchOptions | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search via LangSearch Web Search API.

    Returns results parsed from the Bing-compatible response shape
    ``data.webPages.value``.
    """
    if not query.strip() or num_results < 1:
        return []

    api_key = _get_langsearch_api_key()
    url = f"{settings.langsearch_base_url}/v1/web-search"
    # LangSearch count range is 1-10 (default 10).
    count = min(max(num_results, 1), 10)
    # Relative bucket -> LangSearch freshness token; absolute windows have no
    # native param and are covered by the pipeline post-filter.
    bucket = (
        search_options.temporal.bucket
        if search_options is not None and search_options.temporal is not None
        else None
    )
    freshness = langsearch_freshness(bucket) or "noLimit"
    payload = {
        "query": query,
        "freshness": freshness,
        "summary": False,
        "count": count,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = settings.search_retrieve_budget_seconds

    async def _request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(
            url, headers=headers, json=payload, timeout=httpx.Timeout(timeout)
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise LangSearchError("LangSearch response was not valid JSON.") from exc

    def _parse(data: dict[str, Any]) -> list[WebSearchResult]:
        # Response wraps results under "data.webPages.value" (Bing-compatible).
        inner = data.get("data") or {}
        web_pages = inner.get("webPages") or {}
        raw_results = web_pages.get("value", [])
        results: list[WebSearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            link = item.get("url") or ""
            title = item.get("name") or ""
            if not link or not title:
                continue
            snippet = item.get("snippet") or ""
            domain: str | None = None
            try:
                domain = urlparse(link).hostname
            except ValueError:
                pass
            results.append(
                WebSearchResult(
                    title=title.strip(),
                    link=link.strip(),
                    snippet=snippet.strip(),
                    domain=domain,
                    published_date=item.get("datePublished"),
                )
            )
            if len(results) >= count:
                break
        return results

    return await run_provider(
        "langsearch",
        query,
        num_results,
        request=_request,
        parse_response=_parse,
        http_client=http_client,
        timeout_seconds=timeout,
    )
