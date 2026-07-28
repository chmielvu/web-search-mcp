"""Shared Bright Data SERP URL builders, parsing, and error detection."""

from __future__ import annotations

import json
import logging
from urllib.parse import quote_plus


from ...models import WebSearchResult
from ...settings import get_env_value, settings
from ...utils.url_canonicalize import extract_domain_from_url

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
    wrong product zone.
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
