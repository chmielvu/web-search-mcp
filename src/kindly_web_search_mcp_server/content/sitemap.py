from __future__ import annotations

import logging
from typing import Any

from .legacy_sitemap import LegacySitemapConfig, crawl_and_extract_pages
from .tavily_map import extract_map_urls, map_site

LOGGER = logging.getLogger(__name__)


async def crawl_legacy_sitemap(url: str, *, max_depth: int, limit: int) -> dict[str, Any]:
    """Run the legacy Crawl4AI sitemap path."""
    config = LegacySitemapConfig(max_pages=limit, max_depth=max_depth)
    return await crawl_and_extract_pages(url, config=config)


async def generate_sitemap(
    url: str,
    *,
    instructions: str | None = None,
    max_depth: int = 1,
    max_breadth: int = 20,
    limit: int = 50,
    select_paths: list[str] | None = None,
    select_domains: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    allow_external: bool = False,
) -> dict[str, Any]:
    """Generate a sitemap using Tavily Map, with Crawl4AI as fallback.

    Tavily Map is the primary backend and returns the raw Tavily response.
    The legacy Crawl4AI semantic sitemap path is used only if Tavily fails or
    returns no discovered URLs.

    Parameters mirror Tavily Map:
    - instructions: natural-language mapping guidance
    - max_depth: traversal depth, Tavily-supported range 1..5
    - max_breadth: per-level breadth limit
    - limit: maximum total URLs to return
    - select_paths / select_domains: inclusive regex filters
    - exclude_paths / exclude_domains: exclusion regex filters
    - allow_external: follow external links when true
    """
    try:
        tavily_result = await map_site(
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
        if extract_map_urls(tavily_result):
            return tavily_result
        LOGGER.info("generate_sitemap: Tavily Map returned no results; using legacy fallback")
    except Exception as exc:
        LOGGER.warning("generate_sitemap: Tavily Map failed; using legacy fallback: %s", exc)

    return await crawl_legacy_sitemap(url, max_depth=max_depth, limit=limit)
