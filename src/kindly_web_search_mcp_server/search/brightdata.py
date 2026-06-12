"""BrightData SERP provider via MCP StreamableHTTP protocol.

Uses BrightData's MCP endpoint to call search_engine for Google, Bing, Yandex
in parallel. All raw results are concatenated — the pipeline's global RRF
merge handles dedup and scoring.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from ..models import WebSearchResult
from ..retry import retry_with_backoff
from ..settings import get_env_value, settings
from .base_provider import _attach_provider_name


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


async def _search_one_engine(
    query: str,
    engine: str,
    endpoint: str,
) -> list[WebSearchResult]:
    """Search a single engine via BrightData MCP."""
    async with streamablehttp_client(endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "search_engine",
                {
                    "query": query,
                    "engine": engine,
                    "cursor": "0",
                },
            )

    results: list[WebSearchResult] = []
    for content_block in result.content:
        if content_block.type != "text":
            continue
        try:
            data = json.loads(content_block.text)
        except (ValueError, AttributeError):
            continue

        if isinstance(data, dict):
            organic = data.get("organic", [])
        elif isinstance(data, list):
            organic = data
        else:
            continue

        if not isinstance(organic, list):
            continue

        for item in organic:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            link = item.get("link")
            snippet = item.get("snippet") or item.get("description", "")
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


async def search_brightdata(
    query: str,
    *,
    num_results: int,
    http_client: Any = None,
) -> list[WebSearchResult]:
    """Query BrightData MCP endpoint for Google, Bing, Yandex in parallel.

    Uses MCP StreamableHTTP protocol:
    - Endpoint: https://mcp.brightdata.com/mcp?token=<BRIGHTDATA_API_KEY>
    - Calls search_engine tool with engine=google|bing|yandex
    - Results from all engines are concatenated; pipeline RRF handles the rest

    Free tier: https://brightdata.com/mcp
    """
    if not query.strip() or num_results < 1:
        return []

    endpoint = _get_endpoint()
    engines = ["google", "bing", "yandex"]

    async def _do_search() -> list[WebSearchResult]:
        engine_results_raw = await asyncio.gather(
            *[_search_one_engine(query, e, endpoint) for e in engines],
            return_exceptions=True,
        )

        all_results: list[WebSearchResult] = []
        for raw in engine_results_raw:
            if isinstance(raw, BaseException):
                continue
            if raw:
                all_results.extend(raw)

        return all_results[:num_results]

    results = await retry_with_backoff(
        _do_search,
        provider_name="brightdata",
        max_retries=2,
    )
    return _attach_provider_name(results, "brightdata")[:num_results]
