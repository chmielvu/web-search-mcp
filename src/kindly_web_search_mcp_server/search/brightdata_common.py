"""Shared Bright Data SERP URL builders, parsing, and Bing sidecar helpers."""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import quote_plus

import httpx

from ..models import WebSearchResult
from ..settings import get_env_value, settings

logger = logging.getLogger(__name__)

_REST_ENDPOINT = "https://api.brightdata.com/request"

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


class BrightDataError(RuntimeError):
    pass


class BrightDataConfigError(BrightDataError):
    pass


def get_brightdata_api_key() -> str:
    api_key = get_env_value("BRIGHTDATA_API_KEY", settings.brightdata_api_key).strip()
    if not api_key:
        raise BrightDataConfigError(
            "BRIGHTDATA_API_KEY is not set. Configure it in your runtime settings."
        )
    return api_key


def resolve_payload_base() -> dict:
    zone = get_env_value("BRIGHTDATA_ZONE", settings.brightdata_zone).strip() or "sdk_serp"
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
        token = brightdata_freshness_token(freshness)
        if token:
            url += f"&tbs=qdr:{token}"
    return url


def build_bing_url(
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
    url += "&brd_json=1"
    return url


def build_yandex_url(
    query: str,
    region: str = "84",
    language: str = "en",
) -> str:
    q = quote_plus(query)
    url = f"https://www.yandex.com/search/?text={q}"
    if region:
        url += f"&lr={region}"
    if language:
        url += f"&lang={language}"
    return url


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


def parse_brightdata_response(
    data: dict, search_type: str, num_results: int
) -> list[WebSearchResult]:
    upstream = detect_upstream_error(data)
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
                        published_date=item.get("date"),
                    )
                )
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
                    )
                )
                if len(results) >= num_results:
                    break

    return results


async def search_bing_sidecar(
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
    bing_timeout = settings.brightdata_bing_timeout_seconds

    try:

        async def _do_bing(client: httpx.AsyncClient) -> list[WebSearchResult]:
            response = await client.post(
                _REST_ENDPOINT,
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
            result = await asyncio.wait_for(_do_bing(http_client), timeout=bing_timeout)
        else:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=bing_timeout)
            ) as client:
                result = await asyncio.wait_for(_do_bing(client), timeout=bing_timeout)
        return result
    except asyncio.TimeoutError:
        logger.debug("BrightData Bing search timed out after %.1fs", bing_timeout)
        return []
    except asyncio.CancelledError:
        logger.debug("BrightData Bing search cancelled")
        raise
    except Exception:
        logger.debug("BrightData Bing search failed", exc_info=True)
        return []


async def collect_bing_sidecar(
    bing_future: asyncio.Future[list[WebSearchResult]],
    *,
    grace_seconds: float,
) -> list[WebSearchResult]:
    def _ignore_task_exception(task: asyncio.Future[list[WebSearchResult]]) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    try:
        if bing_future.done():
            return await bing_future
        if grace_seconds > 0:
            return await asyncio.wait_for(asyncio.shield(bing_future), timeout=grace_seconds)
    except asyncio.TimeoutError:
        logger.debug(
            "BrightData Bing sidecar did not finish within %.2fs grace",
            grace_seconds,
        )
    except Exception as exc:
        logger.warning("BrightData Bing failed: %s", exc)
        return []

    bing_future.cancel()
    bing_future.add_done_callback(_ignore_task_exception)
    return []
