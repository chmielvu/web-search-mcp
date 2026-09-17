"""Tavily Search API provider — AI-optimized web search.

Default: ``search_depth="advanced"`` for highest relevance + chunked content.
``search_options`` maps ``domain_boost`` to Tavily's ``include_domains``
and maps ``searxng_time_range`` to the provider's ``time_range``.
``**kwargs`` captures intent-driven ``provider_arguments`` (e.g. ``topic``,
``time_range``, ``country``) defined in ``intent_policy.py``.

Docs: https://docs.tavily.com/documentation/api-reference/endpoint/search
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from ...settings import get_env_value, settings
from ...utils.url_canonicalize import extract_domain_from_url
from ..filters import tavily_time_range
from ..options import SearchOptions
from ..types import EngineCall, SearchHit
from .base import (
    ProviderRequestError,
    ProviderRequestMetadata,
    _with_metadata,
    get_provider_request_metadata,
    provider_retry_max_retries,
    run_provider,
    set_provider_request_metadata,
)


class TavilyError(ProviderRequestError):
    pass


class TavilyConfigError(TavilyError):
    pass


_TAVILY_SEARCH_URL = "https://api.tavily.com/search"

# Provider-argument keys that map directly to Tavily API payload fields.
_TAVILY_ARG_KEYS = frozenset(
    {
        "topic",
        "search_depth",
        "time_range",
        "include_answer",
        "include_published_date",
        "filter_by_published_date",
        "include_raw_content",
        "include_images",
        "include_image_descriptions",
        "include_favicon",
        "country",
        "auto_parameters",
        "exact_match",
        "chunks_per_source",
        "safe_search",
        "include_usage",
    }
)

# Tavily's ``country`` parameter accepts full lowercase country names and is
# only honored for topic=general. Map the alpha-2 codes we normalize to.
_COUNTRY_NAMES: dict[str, str] = {
    "us": "united states",
    "gb": "united kingdom",
    "de": "germany",
    "fr": "france",
    "es": "spain",
    "it": "italy",
    "pl": "poland",
    "nl": "netherlands",
    "br": "brazil",
    "ca": "canada",
    "au": "australia",
    "in": "india",
    "jp": "japan",
    "mx": "mexico",
    "se": "sweden",
    "ch": "switzerland",
}


def _get_tavily_api_key() -> str:
    api_key = get_env_value("TAVILY_API_KEY", settings.tavily_api_key).strip()
    if not api_key:
        raise TavilyConfigError("TAVILY_API_KEY is not set. Configure it in your runtime settings.")
    return api_key


async def search_tavily(
    query: str,
    *,
    num_results: int,
    search_options: SearchOptions | None = None,
    http_client: httpx.AsyncClient | None = None,
    **kwargs: Any,
) -> EngineCall:
    """Query Tavily Search API and return parsed results.

    Parameters
    ----------
    query : str
        Search query (≤400 chars recommended).
    num_results : int
        Maximum results to return (clamped to 0-20 by the API).
    search_options : SearchOptions | None
        Pipeline search options — maps ``domain_boost`` → ``include_domains``
        and ``searxng_time_range`` → ``time_range``.
    http_client : httpx.AsyncClient | None
        Shared HTTP client for connection pooling.
    **kwargs
        Provider-argument overrides from ``intent_policy`` (e.g. ``topic="news"``,
        ``search_depth="basic"``, ``country="united states"``).

    Tavily endpoint:
        POST https://api.tavily.com/search
        docs: https://docs.tavily.com/documentation/api-reference/endpoint/search
    """
    api_key = _get_tavily_api_key()

    # --- base payload ---
    payload: dict[str, Any] = {
        "query": query,
        "max_results": int(num_results),
        "search_depth": "advanced",
    }

    # --- map SearchOptions → Tavily params ---
    if search_options is not None:
        # Temporal: absolute bounds are native (start_date/end_date); the
        # relative bucket is used only when no explicit window was resolved.
        if search_options.temporal is not None:
            temporal = search_options.temporal
            if not temporal.is_empty:
                payload["include_published_date"] = True
                if temporal.bucket is not None:
                    payload["time_range"] = tavily_time_range(temporal.bucket)
                else:
                    if temporal.start is not None:
                        payload["start_date"] = temporal.start.isoformat()
                    if temporal.end is not None:
                        payload["end_date"] = temporal.end.isoformat()
        # Locale: Tavily boosts by language (ISO 639-1) and, for general topic,
        # accepts full lowercase country names — map alpha-2 via a small table.
        if search_options.language:
            payload["language"] = search_options.language
        if search_options.region:
            country_name = _COUNTRY_NAMES.get(search_options.region.lower())
            if country_name:
                payload["country"] = country_name
    # --- merge provider_arguments from **kwargs ---
    for key in _TAVILY_ARG_KEYS:
        if key in kwargs:
            payload[key] = kwargs[key]

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(_TAVILY_SEARCH_URL, headers=headers, json=payload)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise TavilyError("Tavily response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise TavilyError("Tavily response was not a JSON object.")
        # Seed bounded rate-limit headers into the request metadata so
        # successful calls record remaining quota for diagnostics, and
        # 429 paths (handled by run_provider) retain Retry-After.
        meta_headers: dict[str, object] = {}
        for key in (
            "retry-after",
            "x-ratelimit-remaining",
            "x-ratelimit-limit",
            "x-ratelimit-reset",
            "x-ratelimit-type",
        ):
            value = response.headers.get(key)
            if value:
                meta_headers[key.replace("-", "_")] = value[:500]
        if meta_headers:
            metadata = get_provider_request_metadata() or ProviderRequestMetadata(provider="tavily")
            set_provider_request_metadata(
                _with_metadata(
                    metadata,
                    response_meta={**metadata.response_meta, **meta_headers},
                )
            )
        return data

    def _parse_response(data: dict[str, Any]) -> EngineCall:
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise TavilyError("Tavily response missing `results` list.")

        hits: list[SearchHit] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            link = item.get("url")
            snippet = item.get("content")
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(link, str)
                or not link.strip()
                or not isinstance(snippet, str)
            ):
                continue
            domain = extract_domain_from_url(link.strip())
            if not domain:
                continue
            published = item.get("published_date")
            raw_score = item.get("score")
            provider_score = (
                float(raw_score)
                if isinstance(raw_score, (int, float))
                and not isinstance(raw_score, bool)
                and math.isfinite(raw_score)
                else None
            )
            hits.append(
                SearchHit(
                    title=title,
                    url=link,
                    snippet=snippet,
                    domain=domain,
                    adapter="tavily",
                    provider_score=provider_score,
                    published=published
                    if isinstance(published, str) and published.strip()
                    else None,
                )
            )
            if len(hits) >= num_results:
                break

        follow_ups = data.get("follow_up_questions")
        expansion: tuple[str, ...] = ()
        if isinstance(follow_ups, list):
            seeds: list[str] = []
            for question in follow_ups:
                if isinstance(question, str) and question.strip():
                    seeds.append(question.strip())
                if len(seeds) >= 4:
                    break
            expansion = tuple(seeds)
        # The URL-less top-level `answer` cannot become a row: a hit without
        # a URL cannot be cited or fetched (same rule as Bright Data answer
        # entries), and an unrenderable row would consume a final-page slot.
        return EngineCall(adapter="tavily", query=query, hits=tuple(hits), expansion=expansion)

    return await run_provider(
        "tavily",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
        max_retries=provider_retry_max_retries("tavily"),
    )
