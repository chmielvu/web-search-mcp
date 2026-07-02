"""BrightData SERP API provider via direct REST API.

Uses POST https://api.brightdata.com/request with parsed_light data format
for Google and (optionally) Bing. Google runs through run_provider with
retries; Bing is fire-and-forget with a short timeout.
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import quote_plus

import httpx

from ..models import WebSearchResult
from ..settings import get_env_value, settings
from .base_provider import run_provider

logger = logging.getLogger(__name__)

_REST_ENDPOINT = "https://api.brightdata.com/request"


class BrightDataError(RuntimeError):
    pass


class BrightDataConfigError(BrightDataError):
    pass


def _get_brightdata_api_key() -> str:
    api_key = get_env_value("BRIGHTDATA_API_KEY", settings.brightdata_api_key).strip()
    if not api_key:
        raise BrightDataConfigError(
            "BRIGHTDATA_API_KEY is not set. Configure it in your runtime settings."
        )
    return api_key


def _resolve_payload_base() -> dict:
    zone = get_env_value("BRIGHTDATA_ZONE", settings.brightdata_zone).strip() or "sdk_serp"
    payload: dict = {
        "zone": zone,
        "format": "raw",
        "data_format": "parsed_light",
    }
    extra = settings.brightdata_payload_extra
    if extra:
        try:
            payload.update(json.loads(extra))
        except json.JSONDecodeError:
            logger.warning("BRIGHTDATA_PAYLOAD_EXTRA is not valid JSON, ignoring")
    return payload


def _build_google_url(
    query: str,
    country: str = "us",
    language: str = "en",
    search_type: str = "web",
    exact_match: bool = True,
) -> str:
    q = quote_plus(query)
    url = f"https://www.google.com/search?q={q}"
    if country:
        url += f"&gl={country}"
    if language:
        url += f"&hl={language}"
    if exact_match:
        url += "&nfpr=1"
    url += "&brd_json=1"
    if search_type == "news":
        url += "&tbm=nws"
    return url


def _build_bing_url(
    query: str,
    country: str = "us",
    language: str = "en",
) -> str:
    q = quote_plus(query)
    url = f"https://www.bing.com/search?q={q}"
    if country:
        url += f"&cc={country}"
    if language:
        url += f"&setLang={language}-{country.upper()}"
    return url


def _detect_upstream_error(data: dict) -> str | None:
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


def _parse_response(data: dict, search_type: str, num_results: int) -> list[WebSearchResult]:
    upstream = _detect_upstream_error(data)
    if upstream:
        raise BrightDataError(upstream)

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
                if not (isinstance(title, str) and title.strip() and isinstance(link, str) and link.strip()):
                    continue
                results.append(WebSearchResult(
                    title=title.strip(),
                    link=link.strip(),
                    snippet=str(snippet).strip(),
                    published_date=item.get("date"),
                ))
                if len(results) >= num_results:
                    break

    if len(results) == 0:
        organic = data.get("organic", [])
        if isinstance(organic, list):
            for item in organic:
                if not isinstance(item, dict):
                    continue
                title = item.get("title")
                link = item.get("link")
                snippet = item.get("description") or ""
                if not (isinstance(title, str) and title.strip() and isinstance(link, str) and link.strip()):
                    continue
                results.append(WebSearchResult(
                    title=title.strip(),
                    link=link.strip(),
                    snippet=str(snippet).strip(),
                ))
                if len(results) >= num_results:
                    break

    return results


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
    url = _build_bing_url(query, country, language)
    body = {**payload_base, "url": url}
    bing_timeout = settings.brightdata_bing_timeout_seconds

    try:
        async def _do_bing(client: httpx.AsyncClient) -> list[WebSearchResult]:
            response = await client.post(_REST_ENDPOINT, json=body, headers=headers)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise BrightDataError("BrightData Bing response was not valid JSON.") from exc
            if not isinstance(data, dict):
                raise BrightDataError("BrightData Bing response was not a JSON object.")
            return _parse_response(data, "web", num_results)

        if http_client is not None:
            result = await asyncio.wait_for(_do_bing(http_client), timeout=bing_timeout)
        else:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=15.0),
            ) as client:
                result = await asyncio.wait_for(_do_bing(client), timeout=bing_timeout)
        return result
    except (asyncio.TimeoutError, Exception):
        logger.debug("BrightData Bing search skipped or timed out", exc_info=True)
        return []


async def search_brightdata(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    country: str = "us",
    language: str = "en",
    search_type: str = "web",
    exact_match: bool = True,
    use_bing: bool = True,
) -> list[WebSearchResult]:
    """Query Bright Data SERP API via direct REST call for Google + optional Bing.

    Google runs through ``run_provider`` with retry logic and health tracking.
    Bing is a fire-and-forget call with a short timeout; failure does not
    prevent Google results from being returned.

    Args:
        query: The search query.
        num_results: Maximum number of results to return.
        http_client: Optional shared HTTP client.
        country: 2-letter country code for geo-targeting (default: "us").
        language: 2-letter language code (default: "en").
        search_type: "web" or "news" (default: "web").
        exact_match: If True, adds nfpr=1 to disable Google's autocorrect.
        use_bing: If True, also queries Bing (default: True).
    """
    if not query.strip() or num_results < 1:
        return []

    api_key = _get_brightdata_api_key()
    payload_base = _resolve_payload_base()
    req_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    is_news = search_type == "news"

    # --- Google (primary, with retries via run_provider) ---
    async def _google_request(client: httpx.AsyncClient) -> dict:
        google_url = _build_google_url(query, country, language, search_type, exact_match)
        body = {**payload_base, "url": google_url}
        response = await client.post(_REST_ENDPOINT, json=body, headers=req_headers)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise BrightDataError("BrightData response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise BrightDataError("BrightData response was not a JSON object.")
        return data

    def _google_parse(data: dict) -> list[WebSearchResult]:
        return _parse_response(data, search_type, num_results)

    google_results = await run_provider(
        "brightdata",
        query,
        num_results,
        request=_google_request,
        parse_response=_google_parse,
        http_client=http_client,
    )

    # --- Bing (fire-and-forget, no retries; skip for news) ---
    bing_results: list[WebSearchResult] = []
    if use_bing and not is_news:
        bing_results = await _search_bing(
            query, num_results, http_client, api_key,
            payload_base, req_headers, country, language,
        )
        bing_results = bing_results[:num_results]

    merged = google_results + bing_results
    if not merged:
        raise BrightDataError(f"BrightData returned no results for query={query!r}")
    return merged[:num_results]
