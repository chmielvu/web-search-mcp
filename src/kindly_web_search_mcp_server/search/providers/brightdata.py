"""Bright Data SERP API provider via direct REST (Google/Bing/Yandex aliases)."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

from ...models import WebSearchResult
from ...settings import settings
from .base import ProviderRequestError, RequestFn, run_provider
from .brightdata_common import (
    BrightDataError,
    build_bing_url,
    build_google_url,
    build_yandex_url,
    get_brightdata_api_key,
    parse_brightdata_response,
    resolve_payload_base,
    yandex_region_for_country,
)

_SUPPORTED_PROVIDERS = frozenset({"brightdata", "brightdata_bing", "brightdata_yandex"})
_PAGE_SIZE = 10
_MAX_PAGE_COUNT = 10
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
TResponse = TypeVar("TResponse")


def _retry_delay(error: ProviderRequestError, remaining_seconds: float) -> float | None:
    """Return one small retry delay when the remaining request budget allows it."""
    raw_delay = error.metadata.response_meta.get("retry_after")
    try:
        delay = float(raw_delay) if raw_delay is not None else 0.1
    except (TypeError, ValueError):
        delay = 0.1
    if delay < 0:
        return None
    delay = min(delay, 2.0)
    return delay if delay < remaining_seconds else None


async def _run_page(
    provider_name: str,
    query: str,
    page_limit: int,
    *,
    page_index: int,
    request_factory: Callable[[int, float], RequestFn[TResponse]],
    parse_response: Callable[[TResponse], list[WebSearchResult]],
    http_client: httpx.AsyncClient | None,
    deadline: float,
) -> list[WebSearchResult]:
    """Run one page and allow at most one budget-aware transient retry."""
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return []
        try:
            return await run_provider(
                provider_name=provider_name,
                query=query,
                num_results=page_limit,
                request=request_factory(page_index, remaining),
                parse_response=parse_response,
                http_client=http_client,
                timeout_seconds=remaining,
            )
        except ProviderRequestError as exc:
            if attempt or exc.metadata.http_status not in _RETRYABLE_STATUS_CODES:
                raise
            remaining = deadline - time.monotonic()
            delay = _retry_delay(exc, remaining)
            if delay is None:
                raise
            await asyncio.sleep(delay)
    return []


async def _run_paginated(
    provider_name: str,
    query: str,
    num_results: int,
    *,
    request_factory: Callable[[int, float], RequestFn[TResponse]],
    parse_response: Callable[[TResponse], list[WebSearchResult]],
    http_client: httpx.AsyncClient | None,
    timeout_seconds: float,
) -> list[WebSearchResult]:
    """Fetch only the bounded pages needed to satisfy ``num_results``."""
    if http_client is None:
        # Reuse one connection pool across pages for direct callers. The
        # normal search path already injects the shared run-level client.
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as owned_client:
            return await _run_paginated(
                provider_name,
                query,
                num_results,
                request_factory=request_factory,
                parse_response=parse_response,
                http_client=owned_client,
                timeout_seconds=timeout_seconds,
            )

    page_limit = min(_PAGE_SIZE, num_results)
    page_count = min(_MAX_PAGE_COUNT, max(1, math.ceil(num_results / _PAGE_SIZE)))
    deadline = time.monotonic() + timeout_seconds
    results: list[WebSearchResult] = []
    seen_links: set[str] = set()

    for page_index in range(page_count):
        page_results = await _run_page(
            provider_name,
            query,
            page_limit,
            page_index=page_index,
            request_factory=request_factory,
            parse_response=parse_response,
            http_client=http_client,
            deadline=deadline,
        )
        added = 0
        for result in page_results:
            key = result.link.strip().rstrip("/").casefold()
            if key in seen_links:
                continue
            seen_links.add(key)
            results.append(result)
            added += 1
            if len(results) >= num_results:
                return results[:num_results]
        if len(page_results) < page_limit or added == 0:
            break
        if deadline - time.monotonic() <= 0:
            break
    return results[:num_results]


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
    yandex_region: str | None = None,
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
            yandex_region=yandex_region or yandex_region_for_country(country),
            language=language,
        )

    google_timeout = settings.search_retrieve_budget_seconds
    page_limit = min(_PAGE_SIZE, num_results)
    use_light_json = search_type == "web" and num_results <= _PAGE_SIZE

    def _google_request_factory(page_index: int, request_timeout: float) -> RequestFn[dict]:
        async def _request(client: httpx.AsyncClient) -> dict:
            google_url = build_google_url(
                query,
                country,
                language,
                search_type,
                exact_match,
                freshness,
                start=page_index * _PAGE_SIZE,
            )
            body = {**payload_base, "url": google_url}
            if use_light_json:
                # Bright Data's current direct REST docs recommend this
                # parsed top-10 format for lower latency and smaller payloads.
                body["data_format"] = "parsed_light"
            response = await client.post(
                _endpoint(),
                json=body,
                headers=req_headers,
                timeout=httpx.Timeout(request_timeout),
            )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise BrightDataError("BrightData response was not valid JSON.") from exc
            if not isinstance(data, dict):
                raise BrightDataError("BrightData response was not a JSON object.")
            return data

        return _request

    def _google_parse(data: dict) -> list[WebSearchResult]:
        return parse_brightdata_response(data, search_type, page_limit)

    return await _run_paginated(
        provider_name,
        query,
        num_results,
        request_factory=_google_request_factory,
        parse_response=_google_parse,
        http_client=http_client,
        timeout_seconds=google_timeout,
    )


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
    del api_key
    bing_timeout = settings.search_retrieve_budget_seconds
    page_limit = min(_PAGE_SIZE, num_results)

    def _request_factory(page_index: int, request_timeout: float) -> RequestFn[dict]:
        async def _request(client: httpx.AsyncClient) -> dict:
            url = build_bing_url(
                query,
                country,
                language,
                first=1 + page_index * _PAGE_SIZE,
            )
            body = {**payload_base, "url": url}
            response = await client.post(
                _endpoint(),
                json=body,
                headers=headers,
                timeout=httpx.Timeout(request_timeout),
            )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise BrightDataError("BrightData Bing response was not valid JSON.") from exc
            if not isinstance(data, dict):
                raise BrightDataError("BrightData Bing response was not a JSON object.")
            return data

        return _request

    def _parse(data: dict) -> list[WebSearchResult]:
        return parse_brightdata_response(data, "web", page_limit)

    return await _run_paginated(
        provider_name="brightdata_bing",
        query=query,
        num_results=num_results,
        request_factory=_request_factory,
        parse_response=_parse,
        http_client=http_client,
        timeout_seconds=bing_timeout,
    )


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
    yandex_region: str | None,
    language: str,
) -> list[WebSearchResult]:
    """Fetch Yandex SERP via BrightData and parse the raw HTML response."""
    yandex_timeout = settings.search_retrieve_budget_seconds
    page_limit = min(_PAGE_SIZE, num_results)

    def _request_factory(page_index: int, request_timeout: float) -> RequestFn[str]:
        async def _request(client: httpx.AsyncClient) -> str:
            yandex_url = build_yandex_url(
                query,
                yandex_region,
                language,
                page=page_index + 1 if num_results > _PAGE_SIZE else None,
            )
            body = {**payload_base, "url": yandex_url}
            response = await client.post(
                _endpoint(),
                json=body,
                headers=req_headers,
                timeout=httpx.Timeout(request_timeout),
            )
            response.raise_for_status()
            return response.text

        return _request

    def _parse(html: str) -> list[WebSearchResult]:
        return parse_yandex_html_response(html, page_limit)

    return await _run_paginated(
        provider_name="brightdata_yandex",
        query=query,
        num_results=num_results,
        request_factory=_request_factory,
        parse_response=_parse,
        http_client=http_client,
        timeout_seconds=yandex_timeout,
    )
