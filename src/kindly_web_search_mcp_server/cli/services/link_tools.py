from __future__ import annotations

from typing import Any

from ...composio_tools import (
    _composio_similarlinks_impl,
)
from ...content.link_discovery import discover_links


async def fetch_discover_links_payload(
    url: str,
    *,
    max_links: int,
    include_external: bool,
    same_domain_only: bool,
    strip_selectors: str | None,
) -> dict[str, Any]:
    return await discover_links(
        url,
        max_links=max_links,
        include_external=include_external,
        same_domain_only=same_domain_only,
        strip_selectors=strip_selectors,
    )


async def fetch_similar_links_payload(
    url: str,
    *,
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
