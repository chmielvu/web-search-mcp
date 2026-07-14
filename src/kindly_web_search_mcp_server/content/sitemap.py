from __future__ import annotations

import logging
from typing import Any

from .tavily_map import map_site

LOGGER = logging.getLogger(__name__)


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
    """Generate a sitemap using Tavily Map (primary backend, no fallback).

    Parameters mirror Tavily Map:
    - instructions: natural-language mapping guidance
    - max_depth: traversal depth, Tavily-supported range 1..5
    - max_breadth: per-level breadth limit
    - limit: maximum total URLs to return
    - select_paths / select_domains: inclusive regex filters
    - exclude_paths / exclude_domains: exclusion regex filters
    - allow_external: follow external links when true
    """
    return await map_site(
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
