"""BrightData SERP provider via MCP StreamableHTTP protocol.

Uses BrightData's MCP endpoint to call search_engine for Google, Bing, Yandex
with engine selection that prefers the smallest useful set for the query. The
provider parses both the JSON and Markdown shapes returned by BrightData's MCP
tool and surfaces transport failures so provider health can react.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from ..models import WebSearchResult
from ..retry import retry_with_backoff
from ..settings import get_env_value, settings
from .base_provider import _attach_provider_name
from .brightdata_parsing import describe_upstream_error
from .brightdata_parsing import parse_result_text as _parse_result_text
from .provider_health import get_provider_health

logger = logging.getLogger(__name__)

_BRIGHTDATA_ENGINES = ("google", "bing", "yandex")


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


def _get_endpoint() -> str:
    token = _get_brightdata_api_key()
    return f"https://mcp.brightdata.com/mcp?token={token}"


def _engine_health_name(engine: str) -> str:
    return f"brightdata.{engine}"


def _normalize_engines(engines: Iterable[str] | None) -> list[str]:
    if engines is None:
        return []
    normalized: list[str] = []
    for engine in engines:
        candidate = engine.strip().casefold()
        if candidate and candidate in _BRIGHTDATA_ENGINES and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _select_engines(
    query: str,
    preferred_engines: Iterable[str] | None = None,
    *,
    default_engine: str | None = None,
) -> list[str]:
    preferred = _normalize_engines(preferred_engines)
    if preferred:
        return preferred

    normalized_query = query.casefold()
    engines = _normalize_engines([default_engine] if default_engine else [])
    if not engines:
        engines = ["yandex"]
    if engines == ["yandex"] and "google" not in engines:
        engines.append("google")

    if any(
        marker in normalized_query
        for marker in ("latest", "news", "recent", "release", "launch", "today")
    ):
        engines.append("bing")

    if any(
        marker in normalized_query
        for marker in (
            "discussion",
            "compare",
            "comparison",
            "why",
            "opinion",
            "opinions",
            "thread",
        )
    ):
        engines.append("bing")

    if any(ord(char) > 127 for char in query):
        engines.append("yandex")

    if len(normalized_query.split()) >= 12:
        engines.append("bing")

    return _normalize_engines(engines)


async def _search_one_engine(
    query: str,
    engine: str,
    endpoint: str,
) -> list[WebSearchResult]:
    """Search a single engine via BrightData MCP."""
    health_name = _engine_health_name(engine)
    if not get_provider_health().is_healthy(health_name):
        logger.debug("BrightData engine %s is in cooldown, skipping", engine)
        return []

    async with streamablehttp_client(endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            try:
                await session.initialize()
                result = await session.call_tool(
                    "search_engine",
                    {
                        "query": query,
                        "engine": engine,
                    },
                )
            except Exception as exc:
                get_provider_health().mark_failure(health_name)
                logger.warning(
                    "BrightData engine %s failed for query=%r: %s",
                    engine,
                    query,
                    exc,
                )
                raise BrightDataError(
                    f"BrightData engine {engine} failed for query {query!r}"
                ) from exc

    results: list[WebSearchResult] = []
    for content_block in result.content:
        if content_block.type != "text":
            continue
        if not isinstance(content_block.text, str):
            continue
        upstream_error = describe_upstream_error(content_block.text)
        if upstream_error:
            raise BrightDataError(upstream_error)
        results.extend(_parse_result_text(content_block.text))

    get_provider_health().mark_success(health_name)

    return results


async def search_brightdata(
    query: str,
    *,
    num_results: int,
    http_client: Any = None,
    preferred_engines: list[str] | tuple[str, ...] | None = None,
) -> list[WebSearchResult]:
    """Query BrightData MCP endpoint for Google, Bing, Yandex in parallel.

    Uses MCP StreamableHTTP protocol:
    - Endpoint: https://mcp.brightdata.com/mcp?token=<BRIGHTDATA_API_KEY>
    - Calls search_engine tool with a selected subset of google|bing|yandex
    - Results from the selected engines are concatenated; pipeline RRF handles the rest

    Free tier: https://brightdata.com/mcp
    """
    if not query.strip() or num_results < 1:
        return []

    endpoint = _get_endpoint()
    engines = _select_engines(
        query,
        preferred_engines,
        default_engine=settings.brightdata_default_engine,
    )

    async def _do_search() -> list[WebSearchResult]:
        engine_results_raw = await asyncio.gather(
            *[_search_one_engine(query, e, endpoint) for e in engines],
            return_exceptions=True,
        )

        for raw in engine_results_raw:
            if isinstance(raw, asyncio.CancelledError):
                raise raw

        all_results: list[WebSearchResult] = []
        engine_failures: list[str] = []
        for raw in engine_results_raw:
            if isinstance(raw, BaseException):
                engine_failures.append(type(raw).__name__)
                continue
            if raw:
                all_results.extend(raw)

        if not all_results and engine_failures and engines:
            raise BrightDataError(
                f"All BrightData engine attempts failed for query={query!r}: "
                f"{', '.join(engine_failures)}"
            )

        if not all_results and engines:
            raise BrightDataError(
                f"BrightData returned no results for query={query!r} across "
                f"{len(engines)} engine(s): {', '.join(engines)}"
            )

        if engine_failures:
            logger.warning(
                "BrightData returned partial results for query=%r after %d engine failure(s)",
                query,
                len(engine_failures),
            )

        return all_results[:num_results]

    results = await retry_with_backoff(
        _do_search,
        provider_name="brightdata",
        max_retries=2,
    )
    return _attach_provider_name(results, "brightdata")[:num_results]
