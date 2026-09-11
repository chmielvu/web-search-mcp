from __future__ import annotations

import logging
import time
from typing import Annotated

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from ..content.tavily_map import map_site as _generate_sitemap
from ..errors import raise_tool_error
from ..models import SitemapResponse, fetch_next
from ..utils.observability import emit_tool_observability_event
from ._helpers import _record_tool_failure, _record_tool_success

LOGGER = logging.getLogger(__name__)


async def generate_sitemap(
    url: Annotated[
        str,
        Field(description="The target website URL to generate a sitemap from."),
    ],
    instructions: Annotated[
        str | None,
        Field(
            description="Natural-language mapping guidance (e.g. 'Find all blog posts and documentation pages')."
        ),
    ] = None,
    max_depth: Annotated[
        int,
        Field(
            description="Traversal depth (1-5, default 1). Higher values explore deeper site hierarchies.",
        ),
    ] = 1,
    max_breadth: Annotated[
        int,
        Field(
            description="Per-level breadth limit (default 20). Controls how many sibling pages are explored at each depth."
        ),
    ] = 20,
    limit: Annotated[
        int,
        Field(description="Maximum total URLs to return (default 50)."),
    ] = 50,
    select_paths: Annotated[
        list[str] | None,
        Field(
            description="Inclusive regex patterns for URL paths to keep (e.g. ['/docs/.*', '/api/.*'])."
        ),
    ] = None,
    select_domains: Annotated[
        list[str] | None,
        Field(description="Inclusive regex patterns for domains to keep."),
    ] = None,
    exclude_paths: Annotated[
        list[str] | None,
        Field(
            description="Exclusion regex patterns for URL paths to skip (e.g. ['/blog/tag/.*'])."
        ),
    ] = None,
    exclude_domains: Annotated[
        list[str] | None,
        Field(description="Exclusion regex patterns for domains to skip."),
    ] = None,
    allow_external: Annotated[
        bool,
        Field(description="Follow links to external domains when true (default false)."),
    ] = False,
    ctx: Context = CurrentContext(),
) -> SitemapResponse:
    """Generate a structural sitemap for a website: its URL hierarchy and page structure.

    WHEN TO USE:
    - Mapping documentation sites, blogs, or APIs before a deep crawl.
    - Discovering the URL layout of an unfamiliar site.
    - Planning targeted crawls with path or domain regex filters.

    WHEN NOT TO USE:
    - Reading specific pages (use fetch).
    - Expanding one good URL into related coverage (use web_search, then fetch).

    RETURNS:
    - results[]: discovered URLs.
    - related_questions[] and images[] when available.
    - next: suggested fetch calls for promising sections.

    CHAINING: feed discovered URLs to fetch for content reading.

    Args:
        url: The target website URL to map.
        instructions: Natural-language mapping guidance, e.g. "Find all blog
            posts and documentation pages".
        max_depth: Traversal depth (1-5, default 1); higher values explore
            deeper site hierarchies.
        max_breadth: Per-level breadth limit (default 20); controls how many
            sibling pages are explored at each depth.
        limit: Maximum total URLs to return (default 50).
        select_paths: Inclusive regex patterns for URL paths to keep, e.g.
            [\"/docs/.*\", \"/blog/.*\"].
        select_domains: Inclusive regex patterns for domains to keep.
        exclude_paths: Exclusion regex patterns for URL paths to skip, e.g.
            [\"/tag/.*\", \"/category/.*\"].
        exclude_domains: Exclusion regex patterns for domains to skip.
        allow_external: Follow links to external domains when true.
    """
    started = time.monotonic()
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
        duration_ms = (time.monotonic() - started) * 1000.0
        pages_count = (
            len(result.get("results", []))
            if isinstance(result, dict) and "results" in result
            else len(result.get("pages", []))
            if isinstance(result, dict)
            else 0
        )
        emit_tool_observability_event(
            LOGGER,
            "generate_sitemap",
            "response",
            url=url,
            status="success",
            output_count=pages_count,
            duration_ms=duration_ms,
        )
        _record_tool_success(
            "generate_sitemap",
            input_url_count=1,
            output_result_count=pages_count,
        )
        response = SitemapResponse.model_validate(result)
        next_hints = fetch_next(
            response.results,
            why="Fetch promising sections discovered by the site map.",
            confidence="low",
            limit=10,
        )
        if next_hints:
            response.next = next_hints
        return response
    except Exception as e:
        duration_ms = (time.monotonic() - started) * 1000.0
        LOGGER.warning("generate_sitemap error: %s", e, exc_info=True)
        emit_tool_observability_event(
            LOGGER,
            "generate_sitemap",
            "response",
            url=url,
            status="error",
            error_type=type(e).__name__,
            error_message=str(e),
            duration_ms=duration_ms,
        )
        _record_tool_failure("generate_sitemap")
        raise_tool_error(e, provider="tavily_map")
