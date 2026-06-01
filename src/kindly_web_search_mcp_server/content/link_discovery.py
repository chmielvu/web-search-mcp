from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..search.normalize import canonicalize_url
from .safe_fetch import SafeFetchError, safe_fetch_url
from ..scrape.html_tools import (
    extract_html_links,
    extract_html_metadata,
    extract_sitemap_links,
    strip_html_selectors,
)


async def discover_links(
    url: str,
    *,
    max_links: int = 100,
    include_external: bool = True,
    same_domain_only: bool = False,
    strip_selectors: str | None = None,
) -> dict[str, Any]:
    try:
        normalized = canonicalize_url(url)
    except Exception as exc:
        return {
            "input_url": url,
            "normalized_url": url,
            "fetched_url": None,
            "source_type": "unknown",
            "links": [],
            "returned_links": 0,
            "has_more": False,
            "error": {
                "code": type(exc).__name__,
                "message": str(exc),
                "retryable": False,
            },
        }

    try:
        fetched = await safe_fetch_url(url)
    except SafeFetchError as exc:
        return {
            "input_url": url,
            "normalized_url": normalized,
            "fetched_url": None,
            "source_type": "unknown",
            "links": [],
            "returned_links": 0,
            "has_more": False,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "retryable": False,
            },
        }
    except Exception as exc:
        return {
            "input_url": url,
            "normalized_url": normalized,
            "fetched_url": None,
            "source_type": "unknown",
            "links": [],
            "returned_links": 0,
            "has_more": False,
            "error": {
                "code": type(exc).__name__,
                "message": str(exc),
                "retryable": True,
            },
        }

    if fetched.is_pdf:
        fetched_domain = (
            urlparse(fetched.fetched_url).netloc if fetched.fetched_url else ""
        )
        return {
            "input_url": url,
            "normalized_url": normalized,
            "fetched_url": fetched.fetched_url,
            "source_type": "pdf",
            "links": [],
            "returned_links": 0,
            "has_more": False,
            "metadata": {
                "domain": fetched_domain,
            },
        }

    html = fetched.text
    if strip_selectors:
        html = strip_html_selectors(html, strip_selectors)

    metadata = extract_html_metadata(
        html, page_url=url, fetched_url=fetched.fetched_url
    )
    sitemapish = bool("urlset" in html.lower() and "<loc" in html.lower())
    max_links = max(1, max_links)
    link_limit = max_links + 1
    if sitemapish:
        links = extract_sitemap_links(
            html,
            base_url=fetched.fetched_url,
            max_links=link_limit,
            include_external=include_external,
            same_domain_only=same_domain_only,
        )
        source_type = "sitemap"
    else:
        links = extract_html_links(
            html,
            base_url=fetched.fetched_url,
            max_links=link_limit,
            include_external=include_external,
            same_domain_only=same_domain_only,
        )
        source_type = "html"

    has_more = len(links) > max_links
    returned_links = links[:max_links]

    return {
        "input_url": url,
        "normalized_url": normalized,
        "fetched_url": fetched.fetched_url,
        "source_type": source_type,
        "links": returned_links,
        "returned_links": len(returned_links),
        "has_more": has_more,
        "metadata": metadata,
    }
