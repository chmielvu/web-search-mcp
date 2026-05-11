"""Standalone MCP tools backed by Composio Search toolkit actions."""
from __future__ import annotations

from typing import Any, Literal

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from mcp.types import ToolAnnotations

from .composio_client import execute_composio_tool
from .models import (
    ImageSearchResponse,
    ImageSearchResult,
    SimilarLinkResult,
    SimilarLinksResponse,
)

SIMILARLINKS_SLUG = "COMPOSIO_SEARCH_EXA_SIMILARLINK"
IMAGE_SEARCH_SLUG = "COMPOSIO_SEARCH_IMAGE"


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


async def _composio_image_search_impl(
    query: str,
    num_results: int,
    page: int,
) -> ImageSearchResponse:
    safe_page = max(0, page)
    data = await execute_composio_tool(
        IMAGE_SEARCH_SLUG,
        {
            "query": query,
            "num": max(1, min(num_results, 100)),
            "ijn": safe_page,
        },
    )
    raw_results = data.get("images_results", [])
    items = raw_results if isinstance(raw_results, list) else []
    results: list[ImageSearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        page_link = item.get("link")
        original_url = item.get("original")
        if not isinstance(title, str) or not isinstance(page_link, str):
            continue
        if not isinstance(original_url, str):
            continue
        thumbnail_url = item.get("thumbnail")
        source = item.get("source")
        results.append(
            ImageSearchResult(
                title=title.strip(),
                source=source.strip() if isinstance(source, str) else None,
                page_link=page_link.strip(),
                original_url=original_url.strip(),
                thumbnail_url=thumbnail_url.strip() if isinstance(thumbnail_url, str) else None,
            )
        )
    return ImageSearchResponse(
        query=query,
        results=results,
        total_results=len(results),
        page=safe_page,
    )


def register_composio_tools(mcp: Any) -> None:
    """Register standalone Composio Search toolkit tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Composio Similarlinks",
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=True,
        )
    )
    async def composio_similarlinks(
        url: str,
        num_results: int = 5,
        search_type: Literal["keyword", "neural", "magic"] = "neural",
        category: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        ctx: Context = CurrentContext(),
    ) -> dict:
        """Find pages similar to a known URL using Composio Similarlinks.

        Returns related URLs with title/link/score only. The observed Composio payload
        does not include snippets or page content; use `get_content()` on selected links
        when page text is needed.
        """
        await ctx.info(f"Finding similar links for: {url[:80]}...")
        response = await _composio_similarlinks_impl(
            url,
            num_results,
            search_type,
            category,
            include_domains,
            exclude_domains,
        )
        await ctx.info(f"Found {response.total_results} similar links")
        return response.model_dump(exclude_none=True)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Composio Image Search",
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=True,
        )
    )
    async def composio_image_search(
        query: str,
        num_results: int = 10,
        page: int = 0,
        ctx: Context = CurrentContext(),
    ) -> dict:
        """Search image metadata and URLs using Composio Image Search.

        Returns image URLs and metadata, not image bytes. URL accessibility and
        licensing/commercial reuse must be verified from the result page.
        """
        await ctx.info(f"Searching images: {query[:80]}...")
        response = await _composio_image_search_impl(query, num_results, page)
        await ctx.info(f"Found {response.total_results} image results")
        return response.model_dump(exclude_none=True)
