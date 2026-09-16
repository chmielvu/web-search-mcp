"""Bright Data SERP API provider: request builders, response parsing, and transport.

Serves the three catalog providers ``brightdata`` (Google), ``brightdata_bing``
and ``brightdata_yandex`` through one direct-REST path
(``POST https://api.brightdata.com/request``).  Google and Bing are parsed from
Bright Data's JSON envelopes; Yandex returns raw HTML and is parsed locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Callable
from typing import TypeVar
from urllib.parse import quote_plus

import httpx

from ...models import WebSearchResult
from ...settings import get_env_value, settings
from ...utils.url_canonicalize import extract_domain_from_url
from .base import ProviderRequestError, RequestFn, run_provider

logger = logging.getLogger(__name__)

_REST_ENDPOINT = "https://api.brightdata.com/request"
_SUPPORTED_PROVIDERS = frozenset({"brightdata", "brightdata_bing", "brightdata_yandex"})
_PAGE_SIZE = 10
_MAX_PAGE_COUNT = 10
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

_BRIGHTDATA_FRESHNESS_MAP: dict[str, str] = {
    "day": "d",
    "week": "w",
    "month": "m",
    "year": "y",
    "pd": "d",
    "pw": "w",
    "pm": "m",
    "py": "y",
}

TResponse = TypeVar("TResponse")


# ------------------------------------------------------------------
# Errors and zone/credential resolution.
# ------------------------------------------------------------------


class BrightDataError(ProviderRequestError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_meta: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_meta = response_meta or {}


class BrightDataConfigError(BrightDataError):
    pass


def get_brightdata_api_key() -> str:
    api_key = get_env_value("BRIGHTDATA_API_KEY", settings.brightdata_api_key).strip()
    if not api_key:
        raise BrightDataConfigError(
            "BRIGHTDATA_API_KEY is not set. Configure it in your runtime settings."
        )
    return api_key


def get_brightdata_zone() -> str:
    """Resolve an explicitly configured SERP zone.

    ``BRIGHTDATA_SERP_ZONE`` is the documented name.  ``BRIGHTDATA_ZONE`` is
    retained as a compatibility alias, but the historical ``sdk_serp``
    fallback is intentionally rejected because it can silently select the
    wrong product zone.  Set ``BRIGHTDATA_SERP_ZONE`` (not the settings
    default) when the account's SERP zone happens to be named ``sdk_serp``.
    """
    zone = get_env_value("BRIGHTDATA_SERP_ZONE", "").strip()
    if not zone:
        zone = get_env_value("BRIGHTDATA_ZONE", "").strip()
    if zone:
        return zone
    if not zone:
        configured_zone = getattr(settings, "brightdata_zone", "")
        if isinstance(configured_zone, str):
            zone = configured_zone.strip()
    if zone and zone != "sdk_serp":
        return zone
    if not zone or zone == "sdk_serp":
        raise BrightDataConfigError(
            "BRIGHTDATA_SERP_ZONE is not configured. Set it to the name of an "
            "existing Bright Data SERP zone. BRIGHTDATA_ZONE is accepted as "
            "a compatibility alias."
        )
    return zone


def resolve_payload_base() -> dict:
    zone = get_brightdata_zone()
    payload: dict = {
        "zone": zone,
        "format": "raw",
    }
    extra = settings.brightdata_payload_extra
    if extra:
        try:
            payload.update(json.loads(extra))
        except json.JSONDecodeError:
            logger.warning("BRIGHTDATA_PAYLOAD_EXTRA is not valid JSON, ignoring")
    return payload


# ------------------------------------------------------------------
# Target-URL builders for the per-engine Google/Bing/Yandex endpoints.
# ------------------------------------------------------------------


def brightdata_freshness_token(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    return _BRIGHTDATA_FRESHNESS_MAP.get(normalized)


def build_google_url(
    query: str,
    country: str = "us",
    language: str = "en",
    search_type: str = "web",
    exact_match: bool = True,
    freshness: str | None = None,
    start: int | None = None,
) -> str:
    q = quote_plus(query)
    url = f"https://www.google.com/search?q={q}"
    if country:
        url += f"&gl={country}"
    if language:
        url += f"&hl={language}"
    if exact_match:
        url += "&nfpr=1"
    if start is not None:
        url += f"&start={max(0, start)}"
    url += "&brd_json=1"
    if search_type == "news":
        url += "&tbm=nws"
        token = brightdata_freshness_token(freshness)
        if token:
            url += f"&tbs=qdr:{token}"
    return url


def build_bing_url(
    query: str,
    country: str = "us",
    language: str = "en",
    first: int | None = None,
) -> str:
    q = quote_plus(query)
    url = f"https://www.bing.com/search?q={q}"
    if country:
        url += f"&cc={country}"
    if language:
        normalized_language = language.strip().replace("_", "-")
        if len(normalized_language) == 2 and country:
            normalized_language = f"{normalized_language.lower()}-{country.upper()}"
        url += f"&setLang={normalized_language}"
    if first is not None:
        url += f"&first={max(1, first)}"
    url += "&brd_json=1"
    return url


def build_yandex_url(
    query: str,
    region: str | None = "84",
    language: str = "en",
    page: int | None = None,
) -> str:
    q = quote_plus(query)
    url = f"https://www.yandex.com/search/?text={q}"
    if region:
        url += f"&lr={region}"
    if language:
        url += f"&lang={language}"
    if page is not None:
        url += f"&p={max(1, page)}"
    return url


def yandex_region_for_country(country: str | None) -> str | None:
    """Return only region mappings verified by the Bright Data docs.

    Bright Data documents numeric Yandex ``lr`` values rather than a complete
    country-code mapping.  Keep the known USA mapping for backwards
    compatibility and require callers to provide ``yandex_region`` for other
    locales instead of silently using USA.
    """
    if not country:
        return None
    return {"us": "84"}.get(country.strip().lower())


# ------------------------------------------------------------------
# Response parsing.
# ------------------------------------------------------------------


def detect_upstream_error(data: dict) -> str | None:
    if not isinstance(data, dict):
        return None
    status_code = data.get("status_code")
    if not isinstance(status_code, int) or status_code < 400:
        return None
    headers = data.get("headers")
    msg = ""
    if isinstance(headers, dict):
        msg = headers.get("x-brd-err-msg") or headers.get("proxy-status") or ""
    body = data.get("body")
    if isinstance(body, str) and body.strip():
        body = body.strip()[:240]
        return f"BrightData upstream {status_code}: {msg or body}"
    return f"BrightData upstream {status_code}: {msg or 'unknown error'}"


def _upstream_response_metadata(data: dict) -> dict[str, object]:
    headers = data.get("headers")
    if not isinstance(headers, dict):
        return {}
    response_meta: dict[str, object] = {}
    for key in ("retry-after", "x-brd-err-msg", "proxy-status"):
        value = headers.get(key)
        if value:
            response_meta[key.replace("-", "_")] = str(value)[:500]
    return response_meta


def parse_brightdata_response(
    data: dict, search_type: str, num_results: int
) -> list[WebSearchResult]:
    upstream = detect_upstream_error(data)
    if upstream:
        status_code = data.get("status_code")
        raise BrightDataError(
            upstream,
            status_code=status_code if isinstance(status_code, int) else None,
            response_meta=_upstream_response_metadata(data),
        )

    results: list[WebSearchResult] = []

    if search_type == "news":
        news = data.get("news", [])
        if isinstance(news, list):
            for item in news:
                if not isinstance(item, dict):
                    continue
                title = item.get("title")
                link = item.get("link")
                snippet = item.get("description") or ""
                if not (
                    isinstance(title, str)
                    and title.strip()
                    and isinstance(link, str)
                    and link.strip()
                ):
                    continue
                results.append(
                    WebSearchResult(
                        title=title.strip(),
                        link=link.strip(),
                        snippet=str(snippet).strip(),
                        domain=extract_domain_from_url(link.strip()),
                        published_date=item.get("date"),
                    )
                )
                if len(results) >= num_results:
                    break

    if len(results) == 0:
        organic = data.get("organic", [])
        if not isinstance(organic, list) or not organic:
            web_pages = data.get("webPages")
            organic = web_pages.get("value", []) if isinstance(web_pages, dict) else []
        if isinstance(organic, list):
            for item in organic:
                if not isinstance(item, dict):
                    continue
                title = item.get("title") or item.get("name")
                link = item.get("link") or item.get("url")
                snippet = item.get("description") or item.get("snippet") or ""
                if not (
                    isinstance(title, str)
                    and title.strip()
                    and isinstance(link, str)
                    and link.strip()
                ):
                    continue
                results.append(
                    WebSearchResult(
                        title=title.strip(),
                        link=link.strip(),
                        snippet=str(snippet).strip(),
                        domain=extract_domain_from_url(link.strip()),
                    )
                )
                if len(results) >= num_results:
                    break

    return results


# ------------------------------------------------------------------
# Bounded page-by-page transport.
# ------------------------------------------------------------------


def _retry_delay(error: ProviderRequestError, remaining_seconds: float) -> float | None:
    """Return one small retry delay when the remaining request budget allows it."""
    raw_delay = getattr(error.metadata, "response_meta", {}).get("retry_after")
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
            status = getattr(exc.metadata, "http_status", None)
            if attempt or status not in _RETRYABLE_STATUS_CODES:
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


# ------------------------------------------------------------------
# Provider entry points.
# ------------------------------------------------------------------


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
                _REST_ENDPOINT,
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


async def _search_bing(
    query: str,
    num_results: int,
    http_client: httpx.AsyncClient | None,
    payload_base: dict,
    headers: dict,
    country: str,
    language: str,
) -> list[WebSearchResult]:
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
                _REST_ENDPOINT,
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
                _REST_ENDPOINT,
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
