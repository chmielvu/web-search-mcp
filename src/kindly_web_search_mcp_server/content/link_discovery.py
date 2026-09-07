from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

from ..utils.url_canonicalize import canonicalize_url
from .safe_fetch import SafeFetchError, safe_fetch_url
from .stages import _stage_extract_links, _stage_extract_metadata


def _soup(html: str):
    if BeautifulSoup is None:
        return None
    return BeautifulSoup(html or "", "html.parser")


def _safe_domain(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.netloc.lower() or None


def _strip_html_selectors(html: str, selectors: str | None) -> str:
    if not selectors:
        return html
    soup = _soup(html)
    if soup is None:
        return html
    for selector in [part.strip() for part in selectors.split(",") if part.strip()]:
        for element in soup.select(selector):
            element.decompose()
    return str(soup)


def _extract_sitemap_links(
    xml_text: str,
    *,
    base_url: str,
    max_links: int = 100,
    include_external: bool = True,
    same_domain_only: bool = False,
) -> list[dict[str, str | bool]]:
    base_domain = _safe_domain(base_url)
    links: list[dict[str, str | bool]] = []
    seen: set[str] = set()
    for raw_url in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text or "", flags=re.I | re.S):
        candidate = raw_url.strip()
        if not candidate:
            continue
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"}:
            continue
        domain = parsed.netloc.lower() or ""
        internal = bool(base_domain and domain == base_domain)
        if same_domain_only or not include_external:
            if not internal:
                continue
        normalized_url = parsed._replace(fragment="").geturl()
        if normalized_url in seen:
            continue
        seen.add(normalized_url)
        links.append(
            {
                "url": normalized_url,
                "text": normalized_url,
                "domain": domain,
                "internal": internal,
            }
        )
        if len(links) >= max_links:
            break
    return links


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
        fetched_domain = urlparse(fetched.fetched_url).netloc if fetched.fetched_url else ""
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
        html = _strip_html_selectors(html, strip_selectors)

    metadata = _stage_extract_metadata(html, page_url=url, fetched_url=fetched.fetched_url)
    sitemapish = bool("urlset" in html.lower() and "<loc" in html.lower())
    max_links = max(1, max_links)
    link_limit = max_links + 1
    if sitemapish:
        links = _extract_sitemap_links(
            html,
            base_url=fetched.fetched_url,
            max_links=link_limit,
            include_external=include_external,
            same_domain_only=same_domain_only,
        )
        source_type = "sitemap"
    else:
        links = _stage_extract_links(
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
