"""Exa semantic web-search provider."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ...models import WebSearchResult
from ...settings import get_env_value, settings
from ..options import SearchOptions
from .base import ProviderRequestError, provider_retry_max_retries, run_provider

LOGGER = logging.getLogger(__name__)


class ExaError(ProviderRequestError):
    """Exa provider failure."""


class ExaConfigError(ExaError):
    """Exa provider configuration failure."""


_EXA_SEARCH_URL = "https://api.exa.ai/search"
_EXA_MAX_QUERY_CHARS = 2_000
# Top-level /search request fields accepted via provider_arguments.
_EXA_ARGUMENT_KEYS = frozenset(
    {
        "type",
        "category",
        "userLocation",
        "moderation",
        "startPublishedDate",
        "endPublishedDate",
    }
)
# Fields merged into the nested ``contents`` object (not top-level).
_EXA_CONTENTS_ARGUMENT_KEYS = frozenset({"maxAgeHours", "livecrawlTimeout"})
# Intent freshness vocabulary mapped to startPublishedDate (ISO 8601).
_EXA_FRESHNESS_SECONDS = {
    "day": 86_400,
    "week": 604_800,
    "month": 2_592_000,
    "year": 31_536_000,
}


def _get_exa_api_key() -> str:
    api_key = get_env_value("EXA_API_KEY", settings.exa_api_key).strip()
    if not api_key:
        raise ExaConfigError("EXA_API_KEY is not set. Configure it in your runtime settings.")
    return api_key


def translate_exa_freshness(value: str | None) -> str | None:
    """Map an intent freshness word to an Exa ``startPublishedDate``.

    Accepts the same vocabulary as other providers (day/week/month/year) and
    returns an ISO 8601 UTC timestamp exactly that far in the past. Raises
    ``ExaError`` for anything else so stale payloads fail loudly.
    """
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized not in _EXA_FRESHNESS_SECONDS:
        raise ExaError(f"Unsupported Exa freshness value: {value!r}")
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_EXA_FRESHNESS_SECONDS[normalized])
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _snippet(item: dict[str, Any]) -> str:
    highlights = item.get("highlights")
    if isinstance(highlights, list):
        values = [value.strip() for value in highlights if isinstance(value, str) and value.strip()]
        if values:
            return " … ".join(values)[:4000]
    for key in ("summary", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:4000]
    return ""


async def search_exa(
    query: str,
    *,
    num_results: int,
    search_options: SearchOptions | None = None,
    http_client: httpx.AsyncClient | None = None,
    **kwargs: Any,
) -> list[WebSearchResult]:
    """Query Exa's native semantic ``/search`` endpoint."""
    api_key = _get_exa_api_key()
    bounded_num = max(1, min(int(num_results), 100))
    payload: dict[str, Any] = {
        "query": query.strip()[:_EXA_MAX_QUERY_CHARS],
        "numResults": bounded_num,
        "type": "fast",
        "contents": {"highlights": True},
    }

    if search_options is not None:
        if search_options.site_filters:
            payload["includeDomains"] = list(search_options.site_filters)[:1200]
        if search_options.domain_filters:
            payload["excludeDomains"] = list(search_options.domain_filters)[:1200]

    unknown = (
        set(kwargs)
        - _EXA_ARGUMENT_KEYS
        - _EXA_CONTENTS_ARGUMENT_KEYS
        - {"freshness"}
    )
    if unknown:
        raise ExaError(f"Unsupported Exa provider arguments: {', '.join(sorted(unknown))}")

    for key in _EXA_ARGUMENT_KEYS:
        if key in kwargs and kwargs[key] is not None:
            payload[key] = kwargs[key]
    for key in _EXA_CONTENTS_ARGUMENT_KEYS:
        if key in kwargs and kwargs[key] is not None:
            payload["contents"][key] = kwargs[key]

    # Intent freshness -> startPublishedDate unless an explicit date wins.
    if kwargs.get("freshness") is not None and "startPublishedDate" not in payload:
        payload["startPublishedDate"] = translate_exa_freshness(kwargs.get("freshness"))

    # Public server default: filter unsafe content unless overridden.
    if "moderation" not in payload:
        payload["moderation"] = True

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "User-Agent": "web-search-mcp/web-search",
    }

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(_EXA_SEARCH_URL, headers=headers, json=payload)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise ExaError("Exa response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise ExaError("Exa response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise ExaError("Exa response missing `results` list.")
        request_id = data.get("requestId")
        cost = data.get("costDollars")
        if isinstance(cost, dict) and isinstance(cost.get("total"), (int, float)):
            LOGGER.debug("exa requestId=%s costDollars=%.6f", request_id, cost.get("total"))
        elif request_id:
            LOGGER.debug("exa requestId=%s", request_id)

        results: list[WebSearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            link = item.get("url")
            if not isinstance(link, str) or not link.strip():
                continue
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                title = link
            snippet = _snippet(item) or title
            published_date = item.get("publishedDate")
            raw_score = item.get("score")
            results.append(
                WebSearchResult(
                    title=title.strip(),
                    link=link.strip(),
                    snippet=snippet,
                    published_date=published_date if isinstance(published_date, str) else None,
                    raw_score=float(raw_score) if isinstance(raw_score, (int, float)) else None,
                )
            )
            if len(results) >= bounded_num:
                break
        return results

    return await run_provider(
        "exa",
        query,
        bounded_num,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
        max_retries=provider_retry_max_retries("exa"),
    )
