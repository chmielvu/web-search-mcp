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
    """Generate a sitemap using Tavily Map, with Crawl4AI as fallback.

    Tavily Map is the primary backend and exposes its native URL mapping
    response. The legacy Crawl4AI semantic sitemap path is only used as a
    fallback when Tavily fails or returns no discovered URLs.

    Parameters mirror Tavily Map:
    - instructions: natural-language mapping guidance
    - max_depth: traversal depth, Tavily-supported range 1..5
    - max_breadth: per-level breadth limit
    - limit: maximum total URLs to return
    - select_paths / select_domains: inclusive regex filters
    - exclude_paths / exclude_domains: exclusion regex filters
    - allow_external: follow external links when true
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
