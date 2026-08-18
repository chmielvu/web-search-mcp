"""DuckDuckGo Search provider using ddgs library.

Free, reliable fallback provider. Uses asyncio.to_thread for blocking ddgs calls.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...models import WebSearchResult
from ...settings import settings
from ...utils.url_canonicalize import extract_domain_from_url
from .base import ProviderRequestError, run_clientless_provider

LOGGER = logging.getLogger(__name__)


class DDGError(ProviderRequestError):
    """DuckDuckGo search error."""

    pass


async def search_ddg(
    query: str,
    *,
    num_results: int,
    http_client: Any = None,  # Not used, ddgs has its own client
) -> list[WebSearchResult]:
    """Search DuckDuckGo using ddgs library.

    Uses asyncio.to_thread for blocking ddgs calls to maintain async compatibility.

    Args:
        query: Search query string
        num_results: Maximum results to return
        http_client: Ignored (ddgs uses its own HTTP client)

    Returns:
        List of WebSearchResult objects from DuckDuckGo
    """
    if not query.strip():
        return []

    if num_results < 1:
        return []

    return await run_clientless_provider(
        "ddg",
        query,
        num_results,
        request=lambda: asyncio.to_thread(
            _search_ddg_sync,
            query,
            num_results,
        ),
        parse_response=lambda results: results,
    )


def _search_ddg_sync(query: str, num_results: int) -> list[WebSearchResult]:
    """Synchronous DDG search (wrapped in thread pool).

    Args:
        query: Search query string
        num_results: Maximum results to return

    Returns:
        List of WebSearchResult objects
    """
    from ddgs import DDGS

    results: list[WebSearchResult] = []

    with DDGS(timeout=settings.search_retrieve_budget_seconds) as ddgs:
        try:
            raw_results = ddgs.text(
                query,
                max_results=num_results,
            )
        except Exception as exc:
            if "No results found" in str(exc):
                LOGGER.debug("DDG returned no results for query=%r", query)
                return []
            raise

        for item in raw_results:
            if not isinstance(item, dict):
                continue

            title = item.get("title")
            link = item.get("href") or item.get("link") or item.get("url")
            snippet = item.get("body") or item.get("description") or item.get("snippet")

            if not isinstance(title, str) or not title.strip():
                continue
            if not isinstance(link, str) or not link.strip():
                continue
            if not isinstance(snippet, str):
                snippet = ""

            link_str = link.strip()
            domain = extract_domain_from_url(link_str)
            results.append(
                WebSearchResult(
                    title=title.strip(),
                    link=link_str,
                    snippet=snippet.strip(),
                    domain=domain,
                    providers=["ddg"],
                )
            )

            if len(results) >= num_results:
                break

    return results
