"""Standalone MCP tools backed by Composio Search toolkit actions."""

from __future__ import annotations

from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from .composio_client import execute_composio_tool
from .models import (
    SimilarLinkResult,
    SimilarLinksResponse,
)
from .tools.catalog import tool_kwargs
from .errors import format_tool_error

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
    return SimilarLinksResponse(url=url, results=results, total_results=len(results))


def register_composio_tools(mcp: Any) -> None:
    """Register standalone Composio Search toolkit tools."""

    @mcp.tool(**tool_kwargs("composio_similarlinks"))
    async def composio_similarlinks(
        url: str,
        num_results: int = 5,
        search_type: str = "neural",
        category: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        ctx: Context = CurrentContext(),
    ) -> dict:
        """Find pages similar to a known URL via neural similarity. Returns related URLs with match scores.
        Use get_content on selected links when page text is needed.
        """
        try:
            response = await _composio_similarlinks_impl(
                url,
                num_results,
                search_type,
                category,
                include_domains,
                exclude_domains,
            )
        except Exception as exc:
            return format_tool_error(exc, provider="composio")
        await ctx.info(f"Found {response.total_results} similar links")
        return response.model_dump(exclude_none=True)
