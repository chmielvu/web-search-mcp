"""URL discovery: page links, sitemap extraction, and Tavily site mapping."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from ..settings import get_env_value, settings
from ..utils.url_canonicalize import canonicalize_url
from .html_tools import extract_links, extract_metadata, soup_from_html, url_hostname
from .http_utils import SafeFetchError, safe_fetch_url


def _strip_html_selectors(html: str, selectors: str | None) -> str:
    if not selectors:
        return html
    soup = soup_from_html(html)
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
    base_domain = url_hostname(base_url)
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
        if (same_domain_only or not include_external) and not internal:
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

    metadata = extract_metadata(html, page_url=url, fetched_url=fetched.fetched_url)
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
        links = extract_links(
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


# ---------------------------------------------------------------------------
# Tavily Map (site-wide URL discovery via the Tavily Map API)
# ---------------------------------------------------------------------------


class TavilyMapError(RuntimeError):
    """Raised when a Tavily Map request fails or returns invalid data."""


@dataclass(frozen=True)
class TavilyMapConfig:
    """Tavily Map request configuration."""

    instructions: str | None = None
    max_depth: int = 1
    max_breadth: int = 20
    limit: int = 50
    select_paths: list[str] | None = None
    select_domains: list[str] | None = None
    exclude_paths: list[str] | None = None
    exclude_domains: list[str] | None = None
    allow_external: bool = False
    timeout: float = 150.0


def _get_tavily_api_key() -> str:
    api_key = get_env_value("TAVILY_API_KEY", settings.tavily_api_key).strip()
    if not api_key:
        raise TavilyMapError("TAVILY_API_KEY is not set. Configure it in your runtime settings.")
    return api_key


def _map_payload(
    url: str,
    *,
    config: TavilyMapConfig,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "url": url,
        "max_depth": int(config.max_depth),
        "max_breadth": int(config.max_breadth),
        "limit": int(config.limit),
        "allow_external": bool(config.allow_external),
    }
    if config.instructions:
        payload["instructions"] = config.instructions
    if config.select_paths:
        payload["select_paths"] = config.select_paths
    if config.select_domains:
        payload["select_domains"] = config.select_domains
    if config.exclude_paths:
        payload["exclude_paths"] = config.exclude_paths
    if config.exclude_domains:
        payload["exclude_domains"] = config.exclude_domains
    return payload


async def map_site(
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
    timeout: float = 150.0,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Call Tavily Map and return the raw response payload."""
    api_key = _get_tavily_api_key()
    config = TavilyMapConfig(
        instructions=instructions,
        max_depth=max_depth,
        max_breadth=max_breadth,
        limit=limit,
        select_paths=select_paths,
        select_domains=select_domains,
        exclude_paths=exclude_paths,
        exclude_domains=exclude_domains,
        allow_external=allow_external,
        timeout=timeout,
    )
    payload = _map_payload(url, config=config)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    endpoint = "https://api.tavily.com/map"

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise TavilyMapError("Tavily Map response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise TavilyMapError("Tavily Map response was not a JSON object.")
        return data

    if http_client is not None:
        return await _do_request(http_client)

    timeout_config = httpx.Timeout(timeout, connect=min(10.0, timeout))
    async with httpx.AsyncClient(timeout=timeout_config, follow_redirects=True) as client:
        return await _do_request(client)
