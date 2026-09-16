"""DuckDuckGo Search provider using ddgs library.

Free, reliable fallback provider. Uses asyncio.to_thread for blocking ddgs calls.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ...models import WebSearchResult
from ...settings import settings
from ...utils.url_canonicalize import extract_domain_from_url
from ..filters import ddg_timelimit
from ..options import SearchOptions
from .base import ProviderRequestError, run_clientless_provider

LOGGER = logging.getLogger(__name__)

# DDG engines occasionally merge a "sitelinks" bundle into one row: the title
# is 2+ distinct page titles joined by "..." (e.g. "Main Paper Title ...Sub
# Page: Docs ...Another Page Title"). Keep only the first segment — it is the
# primary hit the href/snippet belong to; trailing segments are other URLs'
# titles and would fabricate a multi-topic citation.
_SITELINK_JOIN_RE = re.compile(r"\s*\.{3,}\s*")


def _split_sitelink_title(title: str) -> str:
    """Collapse a sitelink-bundle title to its primary segment.

    A bundle is 2+ segments of 15+ chars each, where every non-final
    segment lacks a terminal period (real titles rarely end in "..." and
    a single legitimate title containing "..." inside one sentence is
    shorter than the bundle threshold). Conservative by design: single-
    segment titles pass through untouched.
    """
    segments = [s.strip() for s in _SITELINK_JOIN_RE.split(title) if s.strip()]
    if len(segments) < 2 or any(len(s) < 15 for s in segments[:-1]):
        return title
    return segments[0]


class DDGError(ProviderRequestError):
    """DuckDuckGo search error."""

    pass


async def search_ddg(
    query: str,
    *,
    num_results: int,
    search_options: SearchOptions | None = None,
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

    # Temporal/locale: relative bucket -> timelimit; locale -> "xx-yy" region.
    # Absolute windows have no DDG param — the pipeline post-filter covers them.
    timelimit: str | None = None
    region: str | None = None
    if search_options is not None:
        if search_options.temporal is not None:
            timelimit = ddg_timelimit(search_options.temporal.bucket)
        if search_options.region and search_options.language:
            region = f"{search_options.region.lower()}-{search_options.language.lower()}"

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
            timelimit=timelimit,
            region=region,
        ),
        parse_response=lambda results: results,
    )


def _search_ddg_sync(
    query: str,
    num_results: int,
    *,
    category: str = "text",
    backend: str | None = None,
    timelimit: str | None = None,
    region: str | None = None,
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

    # ty: ignore[invalid-argument-type] - ddgs declares (*args, **kwargs); the stub
    # does not model its timeout keyword.
    with DDGS(timeout=settings.search_retrieve_budget_seconds) as ddgs:
        try:
            if is_news:
                raw_results = ddgs.news(
                    query,
                    max_results=num_results,
                    backend=backend,
                    timelimit=timelimit,
                    **({"region": region} if region else {}),
                )
            else:
                raw_results = ddgs.text(
                    query,
                    max_results=num_results,
                    backend=backend,
                    timelimit=timelimit,
                    **({"region": region} if region else {}),
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
                    title=_split_sitelink_title(title.strip()),
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
