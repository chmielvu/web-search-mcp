"""Bright Data SERP API provider via direct REST (Google/Bing/Yandex aliases)."""

from __future__ import annotations

import asyncio
import logging

import httpx

from ...models import WebSearchResult
from ...settings import settings
from .base import run_provider
from .brightdata_common import (
    BrightDataError,
    build_bing_url,
    build_google_url,
    build_yandex_url,
    get_brightdata_api_key,
    parse_brightdata_response,
    resolve_payload_base,
)

logger = logging.getLogger(__name__)


_SUPPORTED_PROVIDERS = frozenset({"brightdata", "brightdata_bing", "brightdata_yandex"})


async def search_brightdata(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    country: str = "us",
    language: str = "en",
    search_type: str = "web",
    exact_match: bool = True,
    freshness: str | None = None,
    provider_name: str = "brightdata",
    yandex_region: str = "84",
) -> list[WebSearchResult]:
    if not query.strip() or num_results < 1:
        return []
    if provider_name not in _SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported Bright Data provider: {provider_name}")

    api_key = get_brightdata_api_key()
    payload_base = resolve_payload_base()
    req_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if provider_name == "brightdata_bing":
        return await _search_bing(
            query,
            num_results,
            http_client,
            api_key,
            payload_base,
            req_headers,
            country,
            language,
        )

    if provider_name == "brightdata_yandex":
        return await _search_yandex(
            query,
            num_results=num_results,
            http_client=http_client,
            payload_base=payload_base,
            req_headers=req_headers,
            yandex_url=build_yandex_url(query, yandex_region, language),
        )

    google_timeout = settings.search_retrieve_budget_seconds

    async def _google_request(client: httpx.AsyncClient) -> dict:
        google_url = build_google_url(query, country, language, search_type, exact_match, freshness)
        body = {**payload_base, "url": google_url}
        response = await client.post(
            _endpoint(),
            json=body,
            headers=req_headers,
            timeout=httpx.Timeout(google_timeout),
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise BrightDataError("BrightData response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise BrightDataError("BrightData response was not a JSON object.")
        return data

    def _google_parse(data: dict) -> list[WebSearchResult]:
        return parse_brightdata_response(data, search_type, num_results)

    try:
        return await run_provider(
            provider_name,
            query,
            num_results,
            request=_google_request,
            parse_response=_google_parse,
            http_client=http_client,
            timeout_seconds=google_timeout,
        )
    except Exception as exc:
        logger.warning("BrightData Google failed: %s", exc)
        return []


def _endpoint() -> str:
    from .brightdata_common import _REST_ENDPOINT

    return _REST_ENDPOINT


async def _search_primary(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None,
    payload_base: dict,
    req_headers: dict,
    provider_name: str,
    search_type: str,
    url: str,
) -> list[WebSearchResult]:
    timeout = settings.search_retrieve_budget_seconds

    async def _request(client: httpx.AsyncClient) -> dict:
        body = {**payload_base, "url": url}
        response = await client.post(
            _endpoint(),
            json=body,
            headers=req_headers,
            timeout=httpx.Timeout(timeout),
        )
        response.raise_for_status()
        raw_body = response.text[:500] if response.text else ""
        try:
            data = response.json()
        except ValueError as exc:
            raise BrightDataError(
                f"BrightData {provider_name} response was not valid JSON. "
                f"Status={response.status_code} CT={response.headers.get('content-type', '?')} "
                f"Body={raw_body[:200]}"
            ) from exc
        if not isinstance(data, dict):
            raise BrightDataError(
                f"BrightData {provider_name} response was not a JSON object. Body={raw_body[:200]}"
            )
        return data

    def _parse(data: dict) -> list[WebSearchResult]:
        return parse_brightdata_response(data, search_type, num_results)

    return await run_provider(
        provider_name,
        query,
        num_results,
        request=_request,
        parse_response=_parse,
        http_client=http_client,
        timeout_seconds=timeout,
    )


async def _search_bing(
    query: str,
    num_results: int,
    http_client: httpx.AsyncClient | None,
    api_key: str,
    payload_base: dict,
    headers: dict,
    country: str,
    language: str,
) -> list[WebSearchResult]:
    url = build_bing_url(query, country, language)
    body = {**payload_base, "url": url}
    bing_timeout = settings.search_retrieve_budget_seconds

    try:

        async def _do_bing(client: httpx.AsyncClient) -> list[WebSearchResult]:
            response = await client.post(
                _endpoint(),
                json=body,
                headers=headers,
                timeout=httpx.Timeout(bing_timeout),
            )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise BrightDataError("BrightData Bing response was not valid JSON.") from exc
            if not isinstance(data, dict):
                raise BrightDataError("BrightData Bing response was not a JSON object.")
            return parse_brightdata_response(data, "web", num_results)

        if http_client is not None:
            return await _do_bing(http_client)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=bing_timeout)
        ) as client:
            return await _do_bing(client)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        logger.debug("BrightData Bing search timed out after %.1fs", bing_timeout)
        return []
    except asyncio.CancelledError:
        logger.debug("BrightData Bing search cancelled")
        raise
    except Exception:
        logger.debug("BrightData Bing search failed", exc_info=True)
        return []


def parse_yandex_html_response(
    html: str,
    num_results: int,
) -> list[WebSearchResult]:
    """Parse organic results from a raw Yandex SERP response."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    results: list[WebSearchResult] = []
    for item in soup.select("li.serp-item, ul#search-result > li"):
        classes = item.get("class", ())
        if "serp-item_type_ad" in classes or item.select_one("[class*='AdvLabel']"):
            continue
        link_tag = item.select_one("a.OrganicTitle-Link[href], h2 a[href], a.Link[href]")
        if link_tag is None:
            continue
        link = str(link_tag.get("href", "")).strip()
        if not link.startswith(("http://", "https://")):
            continue
        title = link_tag.get_text(" ", strip=True)
        if not title:
            continue
        snippet_tag = item.select_one(
            ".OrganicText, .TextContainer, .organic__text, .Organic-ContentWrapper"
        )
        snippet = snippet_tag.get_text(" ", strip=True) if snippet_tag else ""
        results.append(
            WebSearchResult(
                title=title,
                link=link,
                snippet=snippet,
            )
        )
        if len(results) >= num_results:
            break
    return results


async def _search_yandex(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None,
    payload_base: dict,
    req_headers: dict,
    yandex_url: str,
) -> list[WebSearchResult]:
    """Fetch Yandex SERP via BrightData and parse the raw HTML response."""
    body = {**payload_base, "url": yandex_url}

    async def _request(client: httpx.AsyncClient) -> str:
        response = await client.post(
            _endpoint(),
            json=body,
            headers=req_headers,
        )
        response.raise_for_status()
        return response.text

    def _parse(html: str) -> list[WebSearchResult]:
        return parse_yandex_html_response(html, num_results)

    return await run_provider(
        provider_name="brightdata_yandex",
        query=query,
        num_results=num_results,
        request=_request,
        parse_response=_parse,
        http_client=http_client,
        timeout_seconds=settings.search_retrieve_budget_seconds,
    )
