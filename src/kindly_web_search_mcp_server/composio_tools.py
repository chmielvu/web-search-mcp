"""Standalone MCP tools backed by Composio Search toolkit actions."""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any
from uuid import uuid4

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from .composio_client import execute_composio_tool
from .models import (
    SimilarLinkResult,
    SimilarLinksResponse,
    fetch_next,
)
from .tools.catalog import tool_kwargs
from .errors import raise_tool_error
from .utils.observability import emit_tool_observability_event

LOGGER = logging.getLogger(__name__)

SIMILARLINKS_SLUG = "COMPOSIO_SEARCH_EXA_SIMILARLINK"
WEB_SEARCH_SLUG = "COMPOSIO_SEARCH_TAVILY"


def _string_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
    return cleaned or None


def _extract_similar_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    container = data.get("results", data)
    if isinstance(container, dict):
        items = container.get("results", [])
    else:
        items = container
    return items if isinstance(items, list) else []


def _parse_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


async def _composio_similarlinks_impl(
    url: str,
    num_results: int,
    search_type: str,
    category: str | None,
    include_domains: list[str] | None,
    exclude_domains: list[str] | None,
) -> SimilarLinksResponse:
    arguments: dict[str, Any] = {
        "url": url,
        "numResults": max(1, min(num_results, 20)),
        "type": search_type,
    }
    if category:
        arguments["category"] = category
    if include := _string_list(include_domains):
        arguments["includeDomains"] = include
    if exclude := _string_list(exclude_domains):
        arguments["excludeDomains"] = exclude

    data = await execute_composio_tool(SIMILARLINKS_SLUG, arguments)
    results: list[SimilarLinkResult] = []
    for item in _extract_similar_items(data):
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        link = item.get("url")
        if not isinstance(title, str) or not isinstance(link, str):
            continue
        results.append(
            SimilarLinkResult(
                title=title.strip(),
                link=link.strip(),
                score=_parse_float(item.get("score")),
            )
        )
    next_hints = fetch_next(
        [r.link for r in results],
        why="Fetch the related pages for content.",
        confidence="medium",
    )
    return SimilarLinksResponse(
        url=url, results=results, total_results=len(results), next=next_hints
    )


def register_composio_tools(mcp: Any) -> None:
    """Register standalone Composio Search toolkit tools."""

    @mcp.tool(**tool_kwargs("composio_similarlinks"))
    async def composio_similarlinks(
        url: Annotated[str, Field(description="URL to find similar pages for.")],
        num_results: Annotated[
            int,
            Field(
                description="Maximum similar results to request (default 5; values are clamped to 1-20)."
            ),
        ] = 5,
        search_type: Annotated[
            str, Field(description="Similarity search type; keep the default 'neural'.")
        ] = "neural",
        category: Annotated[str | None, Field(description="Optional category filter.")] = None,
        include_domains: Annotated[
            list[str] | None, Field(description="Restrict results to these domains.")
        ] = None,
        exclude_domains: Annotated[
            list[str] | None, Field(description="Exclude results from these domains.")
        ] = None,
        ctx: Context = CurrentContext(),
    ) -> SimilarLinksResponse:
        """Find pages similar to a known URL via neural similarity.

        WHEN TO USE:
        - You already have one good URL and want related pages on the same topic.
        - Expanding a source list from a seed document.
        - Finding alternative coverage, tutorials, or implementations.

        WHEN NOT TO USE:
        - Keyword-based topic search (use web_search or quick_web_search).
        - Reading page content (use fetch).

        RETURNS:
        - results[]: related pages with title, link, and similarity score.
        - total_results: number of related pages returned.
        - next: suggested fetch calls for the top results.

        CHAINING: call fetch on selected links when page text is needed.
        """
        started = time.monotonic()
        tool_call_id = str(uuid4())
        emit_tool_observability_event(
            LOGGER,
            "composio_similarlinks",
            "request",
            tool_call_id=tool_call_id,
            url=url,
            num_results=num_results,
            search_type=search_type,
            category=category,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider="composio",
        )
        try:
            cleaned_url = url.strip()
            if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://")):
                raise ValueError("url must start with http:// or https://")
            response = await _composio_similarlinks_impl(
                cleaned_url,
                num_results,
                search_type,
                category,
                include_domains,
                exclude_domains,
            )
        except Exception as exc:
            emit_tool_observability_event(
                LOGGER,
                "composio_similarlinks",
                "error",
                tool_call_id=tool_call_id,
                url=url,
                provider="composio",
                error_type=type(exc).__name__,
                error_message=str(exc),
                duration_ms=(time.monotonic() - started) * 1000,
            )
            raise_tool_error(exc, provider="composio")
        await ctx.info(f"Found {response.total_results} similar links")
        emit_tool_observability_event(
            LOGGER,
            "composio_similarlinks",
            "response",
            tool_call_id=tool_call_id,
            url=url,
            provider="composio",
            results=response.results,
            output_count=response.total_results,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        return response
