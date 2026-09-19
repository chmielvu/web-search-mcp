from __future__ import annotations

import asyncio
from typing import Any

from ...content.link_discovery import map_site


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
    timeout: float | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "instructions": instructions,
        "max_depth": max_depth,
        "max_breadth": max_breadth,
        "limit": limit,
        "select_paths": select_paths,
        "select_domains": select_domains,
        "exclude_paths": exclude_paths,
        "exclude_domains": exclude_domains,
        "allow_external": allow_external,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    coro = map_site(url, **kwargs)
    if timeout is not None:
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"Sitemap generation timed out after {timeout} seconds.") from None
    return await coro
