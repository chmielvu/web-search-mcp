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
    **kwargs: Any,
) -> list[WebSearchResult]:
    """Search DuckDuckGo using ddgs library.

    Uses asyncio.to_thread for blocking ddgs calls to maintain async compatibility.

    Args:
        query: Search query string
        num_results: Maximum results to return
        http_client: Ignored (ddgs uses its own HTTP client)
        **kwargs: Intent-driven provider arguments (category="news" uses the
            ddgs news backend; backend selects ddgs engines, e.g.
            "grokipedia,wikipedia" or "duckduckgo,yahoo,yandex,brave").

    Returns:
        List of WebSearchResult objects from DuckDuckGo
    """
    if not query.strip():
        return []

    if num_results < 1:
        return []

    category = str(kwargs.get("category") or "text")
    backend = kwargs.get("backend")
    backend_str = str(backend) if backend else None

    return await run_clientless_provider(
        "ddg",
        query,
        num_results,
        request=lambda: asyncio.to_thread(
            _search_ddg_sync,
            query,
            num_results,
            category=category,
            backend=backend_str,
        ),
        parse_response=lambda results: results,
    )


def _search_ddg_sync(
    query: str,
    num_results: int,
    *,
    category: str = "text",
    backend: str | None = None,
) -> list[WebSearchResult]:
    """Synchronous DDG search (wrapped in thread pool).

    Args:
        query: Search query string
        num_results: Maximum results to return
        category: ddgs category: "text" (default) or "news".
        backend: ddgs backend/engine list, e.g. "duckduckgo,yahoo,yandex,brave"
            or "grokipedia,wikipedia". Defaults: "duckduckgo" for text,
            "auto" for news (bing/duckduckgo/yahoo).

    Returns:
        List of WebSearchResult objects
    """
    from ddgs import DDGS

    is_news = category == "news"
    if backend is None:
        backend = "auto" if is_news else "duckduckgo"

    results: list[WebSearchResult] = []

    with DDGS(timeout=settings.search_retrieve_budget_seconds) as ddgs:
        try:
            if is_news:
                raw_results = ddgs.news(
                    query,
                    max_results=num_results,
                    backend=backend,
                )
            else:
                raw_results = ddgs.text(
                    query,
                    max_results=num_results,
                    backend=backend,
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

            published_date = item.get("date") or item.get("published")
            source = item.get("source") or item.get("source_engines")
            source_engines = None
            if isinstance(source, str) and source.strip():
                source_engines = [source.strip()]
            elif isinstance(source, list):
                source_engines = [
                    str(s) for s in source if isinstance(s, str) and s.strip()
                ] or None

            results.append(
                WebSearchResult(
                    title=title.strip(),
                    link=link_str,
                    snippet=snippet.strip(),
                    domain=domain,
                    published_date=str(published_date) if published_date else None,
                    source_engines=source_engines,
                    providers=["ddg"],
                )
            )

            if len(results) >= num_results:
                break

    return results
