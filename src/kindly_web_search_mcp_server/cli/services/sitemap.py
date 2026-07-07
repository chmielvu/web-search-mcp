from __future__ import annotations

from typing import Any

from ...content.sitemap import generate_sitemap


async def fetch_sitemap_payload(
    url: str,
    *,
    instructions: str | None,
    max_depth: int,
    max_breadth: int,
    limit: int,
    select_paths: list[str] | None,
    select_domains: list[str] | None,
    exclude_paths: list[str] | None,
    exclude_domains: list[str] | None,
    allow_external: bool,
) -> dict[str, Any]:
    return await generate_sitemap(
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
