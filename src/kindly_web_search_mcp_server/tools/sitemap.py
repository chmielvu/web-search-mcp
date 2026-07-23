from __future__ import annotations

import logging

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..content.sitemap import generate_sitemap as _generate_sitemap
from ..errors import format_tool_error
from ..utils.observability import emit_tool_observability_event
from ._helpers import _record_tool_failure, _record_tool_success

LOGGER = logging.getLogger(__name__)


async def generate_sitemap(
    url: str,
    instructions: str | None = None,
    max_depth: int = 1,
    max_breadth: int = 20,
    limit: int = 50,
    select_paths: list[str] | None = None,
    select_domains: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    allow_external: bool = False,
    ctx: Context = CurrentContext(),
) -> dict:
    """Generate a structural sitemap for a website using Tavily Map (with Crawl4AI fallback).

    When to use this tool:
    - To map full URL hierarchies and site structures for documentation sites, blogs, or APIs.
    - When planning deep site crawls with specific path regex filters.

    Parameters explained:
    - select_paths: Array of regex strings to include (e.g., ["/docs/.*", "/api/.*"]).
    - exclude_paths: Array of regex strings to skip (e.g., ["/blog/tag/.*"]).

    Args:
        url: The target website URL to generate a sitemap from.
        instructions: Natural-language mapping guidance (e.g., "Find all blog
            posts and documentation pages").
        max_depth: Traversal depth (1-5, default 1). Higher values explore
            deeper site hierarchies.
        max_breadth: Per-level breadth limit (default 20). Controls how many
            sibling pages are explored at each depth.
        limit: Maximum total URLs to return (default 50).
        select_paths: Inclusive regex patterns for URL paths to keep
            (e.g., ["/docs/.*", "/blog/.*"]).
        select_domains: Inclusive regex patterns for domains to keep.
        exclude_paths: Exclusion regex patterns for URL paths to skip
            (e.g., ["/tag/.*", "/category/.*"]).
        exclude_domains: Exclusion regex patterns for domains to skip.
        allow_external: Follow links to external domains when True.
    """
    emit_tool_observability_event(
        LOGGER,
        "generate_sitemap",
        "request",
        url=url,
        instructions=instructions,
        max_depth=max_depth,
        max_breadth=max_breadth,
        limit=limit,
        select_paths=select_paths,
        select_domains=select_domains,
        exclude_paths=exclude_paths,
        exclude_domains=exclude_domains,
        allow_external=allow_external,
    )

    try:
        await ctx.report_progress(progress=0, total=100, message="Mapping site...")
        result = await _generate_sitemap(
            url,
            instructions=instructions,
            max_depth=max_depth,
            max_breadth=max_breadth,
            limit=limit,
            select_paths=select_paths,
            select_domains=select_domains,
            exclude_paths=exclude_paths,
            exclude_domains=exclude_domains,
            allow_external=allow_external,
        )
        await ctx.report_progress(progress=100, total=100, message="Done")
        if isinstance(result, dict) and "results" in result:
            _record_tool_success(
                "generate_sitemap",
                input_url_count=1,
                output_result_count=len(result.get("results", [])),
            )
        else:
            pages = result.get("pages", []) if isinstance(result, dict) else []
            _record_tool_success(
                "generate_sitemap",
                input_url_count=1,
                output_result_count=len(pages),
            )
        return result
    except Exception as e:
        LOGGER.warning("generate_sitemap error: %s", e, exc_info=True)
        _record_tool_failure("generate_sitemap")
        return format_tool_error(e, provider="tavily_map")
