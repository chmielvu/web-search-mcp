from __future__ import annotations

from typing import Any

from langchain.tools import tool

from kindly_web_search_mcp_server.composio_tools import (
    _composio_image_search_impl,
    _composio_similarlinks_impl,
    _quick_web_search_impl,
)
from kindly_web_search_mcp_server.search.brave import search_brave as _search_brave_provider
from kindly_web_search_mcp_server.search.ddg import search_ddg
from kindly_web_search_mcp_server.search.tavily import search_tavily as _search_tavily_provider

from .models import (
    ImageSearchInput,
    SearchInput,
    SimilarLinksInput,
)


def _dump_results(results: list[Any]) -> list[dict[str, Any]]:
    dumped: list[dict[str, Any]] = []
    for item in results:
        if hasattr(item, "model_dump"):
            dumped.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            dumped.append(item)
    return dumped


async def _search_duckduckgo(query: str, num_results: int) -> dict[str, Any]:
    results = await search_ddg(query, num_results=num_results)
    return {
        "tool": "search_duckduckgo",
        "query": query,
        "provider": "ddg",
        "results": _dump_results(results),
    }


async def _search_tavily(query: str, num_results: int) -> dict[str, Any]:
    results = await _search_tavily_provider(query, num_results=num_results)
    return {
        "tool": "search_tavily",
        "query": query,
        "provider": "tavily",
        "results": _dump_results(results),
    }


async def _search_brave(query: str, num_results: int) -> dict[str, Any]:
    results = await _search_brave_provider(query, num_results=num_results)
    return {
        "tool": "search_brave",
        "query": query,
        "provider": "brave",
        "results": _dump_results(results),
    }


async def _composio_web_search(query: str, num_results: int) -> dict[str, Any]:
    response = await _quick_web_search_impl(query)
    return response.model_dump(exclude_none=True)


async def _composio_similarlinks(
    url: str,
    num_results: int,
    search_type: str,
    category: str | None,
    include_domains: list[str] | None,
    exclude_domains: list[str] | None,
) -> dict[str, Any]:
    response = await _composio_similarlinks_impl(
        url,
        num_results,
        search_type,
        category,
        include_domains,
        exclude_domains,
    )
    return response.model_dump(exclude_none=True)


async def _composio_image_search(query: str, num_results: int, page: int) -> dict[str, Any]:
    response = await _composio_image_search_impl(query, num_results, page)
    return response.model_dump(exclude_none=True)


search_duckduckgo = tool(
    "search_duckduckgo",
    args_schema=SearchInput,
    description=(
        "DuckDuckGo discovery search. Use for broad fallback discovery when you want "
        "an independent web index and no paid API dependency."
    ),
)(_search_duckduckgo)

search_tavily = tool(
    "search_tavily",
    args_schema=SearchInput,
    description=(
        "Direct Tavily search. Use for focused discovery with stronger snippets and "
        "current coverage when you have a Tavily API key."
    ),
)(_search_tavily)

search_brave = tool(
    "search_brave",
    args_schema=SearchInput,
    description=(
        "Direct Brave search. Use for a second independent index, especially when "
        "results from one provider look sparse or biased."
    ),
)(_search_brave)

composio_web_search = tool(
    "composio_web_search",
    args_schema=SearchInput,
    description=(
        "Composio Exa-backed web search that returns an answer and citations. Use when "
        "you want a strong broad-search starting point plus source pointers."
    ),
)(_composio_web_search)

composio_similarlinks = tool(
    "composio_similarlinks",
    args_schema=SimilarLinksInput,
    description=(
        "Expand from one known-good URL into nearby pages. Use after you have a strong "
        "seed source and want adjacent pages or related domains."
    ),
)(_composio_similarlinks)

composio_image_search = tool(
    "composio_image_search",
    args_schema=ImageSearchInput,
    description=(
        "Search for image pages and image URLs. Use when the question needs visual "
        "evidence or image-source discovery."
    ),
)(_composio_image_search)


def get_search_tools() -> list[Any]:
    return [
        composio_web_search,
        search_tavily,
        search_brave,
        search_duckduckgo,
        composio_similarlinks,
        composio_image_search,
    ]
