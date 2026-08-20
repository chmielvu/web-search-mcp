"""SerpApi multi-engine search provider.

Supports SerpApi engines (yahoo, naver, bing, etc.) via the
``engine`` query parameter. Google and Baidu engines are disabled.
When ``SERPAPI_ENGINES`` is a comma-separated list, all allowed listed engines
are queried in parallel and their raw results are concatenated — the pipeline's
global RRF merge handles dedup and scoring.

Docs:
  - Yahoo:  https://serpapi.com/yahoo-search-api   (engine=yahoo)
  - Naver:  https://serpapi.com/naver-search-api   (engine=naver)
  - Bing:   https://serpapi.com/bing-search-api    (engine=bing)
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ...models import WebSearchResult
from ...settings import get_env_value, settings
from .base import ProviderRequestError, _attach_provider_name


class SerpApiError(ProviderRequestError):
    pass


class SerpApiConfigError(SerpApiError):
    pass

DISABLED_ENGINES: frozenset[str] = frozenset({"google", "baidu"})


def _is_engine_disabled(engine: str) -> bool:
    """Return True if the specified SerpApi engine is disabled."""
    if not settings.serpapi_enabled:
        return True
    engine_lower = engine.strip().lower()
    disabled_engines = {e.strip().lower() for e in settings.serpapi_disabled_engines}
    disabled_providers = {p.strip().lower() for p in settings.disabled_providers}
    if "*" in disabled_engines or "all" in disabled_engines or "serpapi" in disabled_providers:
        return True
    return (
        engine_lower in DISABLED_ENGINES
        or engine_lower in disabled_engines
        or f"serpapi_{engine_lower}" in disabled_providers
        or engine_lower in disabled_providers
    )


def _get_serpapi_api_key() -> str:
    api_key = get_env_value("SERPAPI_API_KEY", settings.serpapi_api_key).strip()
    if not api_key:
        raise SerpApiConfigError(
            "SERPAPI_API_KEY is not set. Configure it in your runtime settings."
        )
    return api_key


def _get_engines() -> list[str]:
    """Return the list of engines to query.

    Priority: SERPAPI_ENGINES (comma-separated) > SERPAPI_DEFAULT_ENGINE (single).
    Returns an empty list if all engines are disabled.
    """
    if not settings.serpapi_enabled:
        return []
    engines_str = get_env_value("SERPAPI_ENGINES", settings.serpapi_engines).strip()
    if engines_str:
        engines = [
            e.strip()
            for e in engines_str.split(",")
            if e.strip() and not _is_engine_disabled(e)
        ]
        if engines:
            return engines
    # Fall back to single default engine if not disabled
    default = settings.serpapi_default_engine.strip()
    if default and not _is_engine_disabled(default):
        return [default]
    return []

def _parse_organic(data: dict[str, Any], engine: str) -> list[WebSearchResult]:
    """Parse organic results from a SerpApi response.

    Different engines use different keys:
      - google, bing, baidu: ``organic_results``
      - naver: ``web_results``
    """
    # Try standard key first, then naver-specific key
    organic = data.get("organic_results", data.get("web_results", []))
    if not isinstance(organic, list):
        return []

    results: list[WebSearchResult] = []
    for item in organic:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        link = item.get("link")
        snippet = item.get("snippet")
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(link, str)
            or not link.strip()
        ):
            continue
        if not isinstance(snippet, str):
            snippet = ""
        results.append(WebSearchResult(title=title, link=link, snippet=snippet))
    return results


async def _search_one_engine(
    query: str,
    engine: str,
    api_key: str,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Query a single SerpApi engine and return parsed results."""

    url = "https://serpapi.com/search"
    params = {
        "q": query,
        "num": num_results,
        "api_key": api_key,
        "engine": engine,
    }

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(url, params=params)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise SerpApiError(f"SerpApi ({engine}) response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise SerpApiError(f"SerpApi ({engine}) response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        return _parse_organic(data, engine)

    # Import here to avoid circular dependency at module level
    from .base import provider_retry_max_retries, run_provider

    return await run_provider(
        f"serpapi_{engine}",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
        # Engine-suffixed provider names (``serpapi_{engine}``) are not in the
        # catalog, so resolve the retry budget from the base ``serpapi`` entry.
        max_retries=provider_retry_max_retries("serpapi"),
    )


async def search_serpapi(
    query: str,
    *,
    num_results: int,
    http_client: Any = None,
    engine: str | None = None,
) -> list[WebSearchResult]:
    """Query SerpApi across configured engines and return concatenated results."""
    if not query.strip() or num_results < 1:
        return []

    if not settings.serpapi_enabled or (engine and _is_engine_disabled(engine)):
        raise SerpApiConfigError(f"SerpApi engine '{engine or 'all'}' is disabled.")

    engines = [engine] if engine else _get_engines()
    if not engines:
        raise SerpApiConfigError("All SerpApi engines are currently disabled.")

    api_key = _get_serpapi_api_key()
    if len(engines) == 1:
        return await _search_one_engine(query, engines[0], api_key, num_results, http_client)

    # Multi-engine: parallel gather, concatenate all results
    engine_results_raw = await asyncio.gather(
        *[_search_one_engine(query, e, api_key, num_results, http_client) for e in engines],
        return_exceptions=True,
    )

    for raw in engine_results_raw:
        if isinstance(raw, asyncio.CancelledError):
            raise raw

    all_results: list[WebSearchResult] = []
    first_error: BaseException | None = None
    for raw in engine_results_raw:
        if isinstance(raw, BaseException):
            # Keep the first structured failure so an all-engine rate limit
            # or outage surfaces as a warning instead of a silent empty
            # success. ProviderRequestError retains the metadata contract.
            if first_error is None:
                first_error = raw
            continue
        if raw:
            all_results.extend(raw)

    if not all_results and first_error is not None:
        raise first_error

    results = all_results[:num_results]
    return _attach_provider_name(results, "serpapi")[:num_results]
