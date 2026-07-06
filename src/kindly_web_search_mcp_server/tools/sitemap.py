from __future__ import annotations

import logging
from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..errors import format_tool_error
from ..utils.observability import emit_tool_observability_event
from ._helpers import _record_tool_failure, _record_tool_success

LOGGER = logging.getLogger(__name__)


async def generate_semantic_sitemap(
    url: str,
    max_pages: int = 100,
    max_depth: int = 3,
    heading_preview_chars: int = 200,
    generate_llms_txt: bool = False,
    keywords: list[str] | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """Discover and crawl pages from a website, extracting a structured
    hierarchical outline of each page based on headings (H1-H6).

    Uses Crawl4AI remote deep crawl with BestFirstCrawlingStrategy for
    intelligent page discovery and prioritization. When keywords are provided,
    pages are ranked by keyword relevance; otherwise, path depth scoring is used.

    Returns a JSON structure with page URLs, titles, heading-based sections
    with text previews, and crawl statistics.

    Set generate_llms_txt=true to also return an llms.txt-formatted markdown
    summary suitable for direct LLM consumption.
    """
    from ..content.sitemap import SitemapConfig, crawl_and_extract_pages

    emit_tool_observability_event(
        LOGGER,
        "generate_semantic_sitemap",
        "request",
        url=url,
        max_pages=max_pages,
        max_depth=max_depth,
        heading_preview_chars=heading_preview_chars,
        generate_llms_txt=generate_llms_txt,
        keywords=keywords,
    )

    config = SitemapConfig(
        max_pages=max(1, min(max_pages, 500)),
        max_depth=max(1, min(max_depth, 10)),
        heading_preview_chars=max(50, min(heading_preview_chars, 1000)),
        generate_llms_txt=generate_llms_txt,
        keywords=keywords,
    )

    try:
        await ctx.report_progress(progress=0, total=100, message="Discovering URLs...")
        result: dict[str, Any] = await crawl_and_extract_pages(url, config=config)
        await ctx.report_progress(progress=100, total=100, message="Done")
        await ctx.info(
            f"Crawled {result['stats']['pages_crawled']} pages, "
            f"{result['stats']['total_sections']} sections"
        )
        _record_tool_success(
            "generate_semantic_sitemap",
            input_url_count=1,
            output_result_count=result["stats"]["pages_crawled"],
        )
        return result
    except Exception as e:
        LOGGER.warning("generate_semantic_sitemap error: %s", e, exc_info=True)
        _record_tool_failure("generate_semantic_sitemap")
        return format_tool_error(e, provider="crawl4ai_sitemap")
