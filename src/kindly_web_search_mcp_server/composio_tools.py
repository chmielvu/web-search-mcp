"""Standalone MCP tools backed by Composio Search toolkit actions."""

from __future__ import annotations

from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from .composio_client import execute_composio_tool
from .models import (
    QuickWebSearchCitation,
    QuickWebSearchResponse,
    SimilarLinkResult,
    SimilarLinksResponse,
)
from .tools.catalog import tool_kwargs

SIMILARLINKS_SLUG = "COMPOSIO_SEARCH_EXA_SIMILARLINK"
WEB_SEARCH_SLUG = "COMPOSIO_SEARCH_TAVILY"
WEB_SEARCH_MAX_RESULTS = 5


def _string_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    cleaned = [
        value.strip() for value in values if isinstance(value, str) and value.strip()
    ]
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


def _extract_web_search_results(
    data: dict[str, Any],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Extract answer and sources from the Composio Tavily search response.

    The current Composio Tavily tool returns a top-level `answer` plus a
    `results` list. Keep the older nested citation shape accepted so historical
    mocks and cached payloads remain parseable.
    """
    answer = data.get("answer")
    if not isinstance(answer, str):
        nested = data.get("results")
        if isinstance(nested, dict) and isinstance(nested.get("answer"), str):
            answer = nested["answer"]
        else:
            answer = None

    sources = data.get("results")
    if isinstance(sources, list):
        return answer, sources

    if isinstance(sources, dict):
        citations = sources.get("citations")
        if isinstance(citations, list):
            return answer, citations
        nested_results = sources.get("results")
        if isinstance(nested_results, list):
            return answer, nested_results

    return answer, []


async def _quick_web_search_impl(query: str) -> QuickWebSearchResponse:
    """Execute Composio Tavily search and parse the response.

    Composio SEARCH_TAVILY returns a concise answer and a `results` list with
    source title, URL, and content/snippet fields.
    """
    data = await execute_composio_tool(
        WEB_SEARCH_SLUG,
        {
            "query": query,
            "max_results": WEB_SEARCH_MAX_RESULTS,
            "search_depth": "basic",
            "include_answer": True,
            "include_images": False,
            "include_raw_content": False,
        },
    )

    answer, citations_raw = _extract_web_search_results(data)
    citations: list[QuickWebSearchCitation] = []

    for item in citations_raw:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        url = item.get("url")
        snippet = item.get("snippet") or item.get("content")
        citations.append(
            QuickWebSearchCitation(
                title=title.strip() if isinstance(title, str) else None,
                url=url.strip() if isinstance(url, str) else None,
                snippet=snippet.strip() if isinstance(snippet, str) else None,
            )
        )

    return QuickWebSearchResponse(
        query=query,
        answer=answer,
        citations=citations,
        total_citations=len(citations),
    )


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

    @mcp.tool(**tool_kwargs("quick_web_search"))
    async def quick_web_search(
        query: str,
        ctx: Context = CurrentContext(),
    ) -> dict:
        """Fast reconnaissance search. Returns a synthesized answer with citations.
        Use as the initial tool call to scope a topic before deeper research.
        """
        await ctx.info(f"Quick web search: {query[:80]}...")
        response = await _quick_web_search_impl(query)
        await ctx.info(f"Found {response.total_citations} citations")
        return response.model_dump(exclude_none=True)

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
