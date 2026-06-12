"""SerpApi multi-engine search provider.

Supports any SerpApi engine (google, baidu, naver, bing, etc.) via the
``engine`` query parameter.  When ``SERPAPI_ENGINES`` is a comma-separated
list, all listed engines are queried in parallel and results are merged
with reciprocal rank fusion — mirroring the BrightData multi-engine pattern.

Docs:
  - Baidu:  https://serpapi.com/baidu-search-api   (engine=baidu)
  - Naver:  https://serpapi.com/naver-search-api   (engine=naver)
  - Google: https://serpapi.com/search-api         (engine=google)
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..models import WebSearchResult
from ..retry import retry_with_backoff
from ..settings import get_env_value, settings
from .base_provider import _attach_provider_name


class SerpApiError(RuntimeError):
    pass


class SerpApiConfigError(SerpApiError):
    pass


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
    """
    engines_str = get_env_value("SERPAPI_ENGINES", settings.serpapi_engines).strip()
    if engines_str:
        engines = [e.strip() for e in engines_str.split(",") if e.strip()]
        if engines:
            return engines
    # Fall back to single default engine
    default = settings.serpapi_default_engine.strip()
    return [default] if default else ["google"]


def _reciprocal_rank_fusion(
    engine_results: dict[str, list[WebSearchResult]],
    k: int = 60,
) -> list[WebSearchResult]:
    """RRF merge across engines. Each engine's results contribute 1/(k+rank)."""
    scores: dict[str, float] = {}
    result_map: dict[str, WebSearchResult] = {}

    for _engine, results in engine_results.items():
        for rank, r in enumerate(results, start=1):
            url = r.link.strip()
            if url not in scores:
                scores[url] = 0.0
                result_map[url] = r
            scores[url] += 1.0 / (k + rank)

    merged = sorted(
        result_map.values(), key=lambda r: scores[r.link.strip()], reverse=True
    )
    return merged


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
    from .base_provider import run_provider

    return await run_provider(
        f"serpapi_{engine}",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
    )


async def search_serpapi(
    query: str,
    *,
    num_results: int,
    http_client: Any = None,
) -> list[WebSearchResult]:
    """Query SerpApi across configured engines and return merged results.

    When ``SERPAPI_ENGINES`` lists multiple engines (e.g. ``baidu,naver,google``),
    all are queried in parallel and results are merged via reciprocal rank fusion.
    With a single engine, behaves as a simple provider.

    SerpApi endpoint:
    - GET https://serpapi.com/search
    - Params: q, num, api_key, engine
    - Supports: google, baidu, naver, bing, and 40+ other engines

    Docs:
    - https://serpapi.com/baidu-search-api
    - https://serpapi.com/naver-search-api
    """
    if not query.strip() or num_results < 1:
        return []

    api_key = _get_serpapi_api_key()
    engines = _get_engines()

    # Single engine: no need for gather/RRF overhead
    if len(engines) == 1:
        return await _search_one_engine(
            query, engines[0], api_key, num_results, http_client
        )

    # Multi-engine: parallel gather + RRF merge
    async def _do_search() -> list[WebSearchResult]:
        engine_results_raw = await asyncio.gather(
            *[
                _search_one_engine(query, e, api_key, num_results, http_client)
                for e in engines
            ],
            return_exceptions=True,
        )

        engine_results: dict[str, list[WebSearchResult]] = {}
        for engine, raw in zip(engines, engine_results_raw):
            if isinstance(raw, BaseException):
                continue
            if raw:
                engine_results[engine] = raw

        if not engine_results:
            return []

        merged = _reciprocal_rank_fusion(engine_results)
        return merged[:num_results]

    results = await retry_with_backoff(
        _do_search,
        provider_name="serpapi",
        max_retries=2,
    )
    return _attach_provider_name(results, "serpapi")[:num_results]
