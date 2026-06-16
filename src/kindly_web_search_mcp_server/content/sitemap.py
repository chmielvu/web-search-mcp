"""Semantic sitemap generation using Crawl4AI.

Provides URL discovery (via sitemap XML, Common Crawl index, or seed crawl),
content extraction with heading-based section structuring, and optional
llms.txt markdown generation.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    DefaultMarkdownGenerator,
    PruningContentFilter,
)
from crawl4ai import async_database as crawl4ai_async_database

from ..scrape.html_tools import extract_sitemap_links
from ..utils.paths import CACHE_DIR, ensure_duckdb_dirs

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SitemapConfig:
    """Configuration for semantic sitemap generation."""

    max_pages: int = 100
    max_depth: int = 3
    include_external: bool = False
    heading_preview_chars: int = 200
    generate_llms_txt: bool = False
    crawl_timeout_seconds: float = 120.0
    headless: bool = True


@dataclass
class PageSection:
    """A heading-delimited section within a page."""

    level: int
    heading: str
    text_preview: str


@dataclass
class SitemapPage:
    """A single crawled page with its sections."""

    url: str
    title: str
    depth: int
    sections: list[PageSection] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Heading extraction from Markdown
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _extract_sections_from_markdown(
    markdown: str,
    *,
    preview_chars: int = 200,
) -> list[PageSection]:
    """Parse markdown headings into hierarchical sections with text previews."""
    sections: list[PageSection] = []
    matches = list(_HEADING_RE.finditer(markdown))

    for i, match in enumerate(matches):
        level = len(match.group(1))
        heading = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        preview = body[:preview_chars].strip()
        if len(body) > preview_chars:
            preview += "..."
        sections.append(PageSection(level=level, heading=heading, text_preview=preview))

    return sections


def _extract_title_from_markdown(markdown: str) -> str:
    """Extract the first H1 heading as the page title."""
    match = _H1_RE.search(markdown)
    if match:
        return match.group(1).strip()
    # Fallback: first non-empty, non-heading line
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:100]
    return ""


# ---------------------------------------------------------------------------
# llms.txt generation
# ---------------------------------------------------------------------------


def _build_llms_txt_markdown(
    pages: list[SitemapPage],
    *,
    base_url: str,
) -> str:
    """Generate llms.txt formatted markdown from crawled pages."""
    lines = [f"# {base_url}\n"]
    for page in pages:
        title = page.title or page.url
        lines.append(f"## {title}\n")
        lines.append(f"URL: {page.url}\n")
        for section in page.sections:
            indent = "  " * max(0, section.level - 1)
            lines.append(f"{indent}- {section.heading}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# URL discovery
# ---------------------------------------------------------------------------


def _extract_domain(url: str) -> str:
    """Extract the base domain from a URL for AsyncUrlSeeder."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _extract_path(url: str) -> str:
    """Extract the path from a URL, normalizing trailing slash."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return path if path else "/"


def _path_match_score(candidate_url: str, input_path: str) -> int:
    """Score how well a candidate URL's path matches the input path prefix.

    Returns the number of matching path segments from the root, higher = better match.
    """
    candidate_path = _extract_path(candidate_url)
    if candidate_path == input_path:
        return 1000  # exact match
    input_segments = input_path.strip("/").split("/")
    candidate_segments = candidate_path.strip("/").split("/")
    score = 0
    for i, (s, cs) in enumerate(zip(input_segments, candidate_segments)):
        if s == cs:
            score = i + 1
        else:
            break
    # Bonus: candidate path starts with input path prefix
    if candidate_path.startswith(input_path + "/") or candidate_path.startswith(input_path + "#"):
        score = max(score, len(input_segments) + 1)
    return score


# Common language-code path prefixes to de-prioritize vs default/English
_NON_DEFAULT_LANG_PREFIXES = frozenset({
    "ar", "de", "es", "fr", "he", "hi", "id", "it", "ja", "ko",
    "nl", "pl", "pt", "pt-br", "ru", "sv", "th", "tr", "uk", "vi",
    "zh", "zh-cn", "zh-tw",
})


def _is_preferred_path_variant(candidate_url: str, input_path: str) -> bool:
    """Check if a candidate URL is a preferred path variant.

    Deprioritizes non-English language path prefixes when the input URL
    doesn't specify a language.
    """
    candidate_path = _extract_path(candidate_url)
    segments = candidate_path.strip("/").split("/")
    first_segment = segments[0].lower() if segments and segments[0] else ""

    # If input already has a language prefix, respect it
    input_segments = input_path.strip("/").split("/")
    input_first = input_segments[0].lower() if input_segments and input_segments[0] else ""

    if input_first in _NON_DEFAULT_LANG_PREFIXES:
        # User asked for a specific language — accept it
        return True

    # If input has no language prefix, deprioritize non-English variants
    if first_segment in _NON_DEFAULT_LANG_PREFIXES:
        return False

    return True


def _sort_discovered_urls(
    urls: list[str],
    *,
    input_url: str,
) -> list[str]:
    """Sort discovered URLs: prefer path-matching variants, de-prioritize non-English."""
    input_path = _extract_path(input_url)

    def sort_key(u: str) -> tuple[int, int]:
        # Higher match score = better, preferred language = better
        score = _path_match_score(u, input_path)
        preferred = 0 if _is_preferred_path_variant(u, input_path) else 1
        return (-score, preferred)

    return sorted(urls, key=sort_key)


def _looks_like_sitemap_url(url: str) -> bool:
    lower = url.lower()
    return lower.endswith(".xml") or "sitemap" in lower


def _patch_crawl4ai_database() -> Path:
    ensure_duckdb_dirs()
    crawl4ai_root = CACHE_DIR / "crawl4ai"
    crawl4ai_root.mkdir(parents=True, exist_ok=True)
    # Crawl4AI still computes some cache paths from CRAWL4_AI_BASE_DIRECTORY.
    # Point it at a writable workspace-local root so its sqlite caches do not
    # fall back to the user home directory in restricted environments.
    os.environ["CRAWL4_AI_BASE_DIRECTORY"] = str(crawl4ai_root)
    crawl4ai_db_path = crawl4ai_root / "crawl4ai.db"
    crawl4ai_async_database.DB_PATH = str(crawl4ai_db_path)
    crawl4ai_async_database.async_db_manager.db_path = str(crawl4ai_db_path)
    crawl4ai_async_database.async_db_manager.content_paths = (
        crawl4ai_async_database.ensure_content_dirs(str(crawl4ai_root))
    )
    return crawl4ai_root


async def _fetch_xml(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        response = await client.get(url)
        response.raise_for_status()
    except Exception as exc:
        LOGGER.debug("Sitemap fetch failed for %s: %s", url, exc)
        return None
    return response.text


async def _collect_sitemap_urls(
    client: httpx.AsyncClient,
    sitemap_url: str,
    *,
    config: SitemapConfig,
    seen_sitemaps: set[str],
    depth: int = 0,
) -> list[str]:
    if sitemap_url in seen_sitemaps or depth > config.max_depth:
        return []
    seen_sitemaps.add(sitemap_url)

    xml_text = await _fetch_xml(client, sitemap_url)
    if not xml_text:
        return []

    links = extract_sitemap_links(
        xml_text,
        base_url=sitemap_url,
        max_links=config.max_pages + 1,
        include_external=config.include_external,
        same_domain_only=not config.include_external,
    )
    page_urls = [entry["url"] for entry in links if not _looks_like_sitemap_url(entry["url"])]
    if page_urls:
        return page_urls

    discovered: list[str] = []
    for entry in links:
        child_url = entry["url"]
        if not _looks_like_sitemap_url(child_url):
            continue
        child_urls = await _collect_sitemap_urls(
            client,
            child_url,
            config=config,
            seen_sitemaps=seen_sitemaps,
            depth=depth + 1,
        )
        discovered.extend(child_urls)
        if len(discovered) >= config.max_pages:
            break
    return discovered


async def discover_urls(
    url: str,
    *,
    config: SitemapConfig,
) -> list[dict[str, Any]]:
    """Discover URLs from sitemap XML sources."""
    domain = _extract_domain(url)
    sitemap_candidates = [
        f"{domain}/sitemap.xml",
        f"{domain}/sitemap_index.xml",
    ]

    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        # Resolve the input URL's effective path by following redirects
        # (e.g., /introduction → /en/introduction)
        resolved_url = url
        try:
            head_resp = await client.head(url)
            resolved_url = str(head_resp.url)
        except Exception:
            pass

        discovered: list[str] = []
        seen_sitemaps: set[str] = set()
        for sitemap_url in sitemap_candidates:
            urls = await _collect_sitemap_urls(
                client,
                sitemap_url,
                config=config,
                seen_sitemaps=seen_sitemaps,
            )
            if urls:
                discovered.extend(urls)
                break

    if discovered:
        sorted_urls = _sort_discovered_urls(discovered, input_url=resolved_url)
        return [{"url": u} for u in sorted_urls[: config.max_pages]]
    return []


# ---------------------------------------------------------------------------
# Full sitemap crawl
# ---------------------------------------------------------------------------


async def crawl_and_extract_pages(
    url: str,
    *,
    config: SitemapConfig,
) -> dict[str, Any]:
    """Full sitemap crawl: discover URLs, crawl pages, extract sections.

    Returns a dict with keys: query_url, pages, stats, and optionally
    llms_txt_markdown.
    """
    # Phase 1: Discover URLs
    discovered = await discover_urls(url, config=config)
    urls_to_crawl: list[str] = []
    for entry in discovered:
        if isinstance(entry, dict):
            urls_to_crawl.append(entry.get("url", ""))
        elif isinstance(entry, str):
            urls_to_crawl.append(entry)

    if not urls_to_crawl:
        urls_to_crawl = [url]

    urls_to_crawl = urls_to_crawl[: config.max_pages]

    # Phase 2: Crawl pages with content extraction
    crawl4ai_root = _patch_crawl4ai_database()
    browser_config = BrowserConfig(headless=config.headless)
    md_generator = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(),
    )
    run_config = CrawlerRunConfig(
        markdown_generator=md_generator,
        page_timeout=int(config.crawl_timeout_seconds * 1000),
        cache_mode=CacheMode.BYPASS,
        exclude_external_links=config.include_external is False,
        verbose=False,
    )

    pages: list[SitemapPage] = []
    stats: dict[str, int] = {
        "pages_crawled": 0,
        "pages_failed": 0,
        "total_sections": 0,
    }

    async with AsyncWebCrawler(
        config=browser_config,
        base_directory=str(crawl4ai_root),
    ) as crawler:
        results = await crawler.arun_many(
            urls=urls_to_crawl,
            config=run_config,
        )
        # arun_many returns results in completion order, re-sort to input order
        result_by_url: dict[str, Any] = {r.url: r for r in results}
        ordered_results = [result_by_url.get(u) for u in urls_to_crawl]
        for result in ordered_results:
            if not result.success:
                stats["pages_failed"] += 1
                continue
            stats["pages_crawled"] += 1

            md = ""
            if hasattr(result.markdown, "fit_markdown"):
                md = result.markdown.fit_markdown or result.markdown.raw_markdown or ""
            else:
                md = str(result.markdown) if result.markdown else ""

            sections = _extract_sections_from_markdown(
                md, preview_chars=config.heading_preview_chars
            )
            title = _extract_title_from_markdown(md)
            stats["total_sections"] += len(sections)

            pages.append(
                SitemapPage(
                    url=result.url,
                    title=title,
                    depth=0,
                    sections=sections,
                )
            )

    # Phase 3: Build response
    output: dict[str, Any] = {
        "query_url": url,
        "pages": [
            {
                "url": p.url,
                "title": p.title,
                "depth": p.depth,
                "sections": [
                    {
                        "level": s.level,
                        "heading": s.heading,
                        "text_preview": s.text_preview,
                    }
                    for s in p.sections
                ],
            }
            for p in pages
        ],
        "stats": stats,
    }

    if config.generate_llms_txt:
        output["llms_txt_markdown"] = _build_llms_txt_markdown(pages, base_url=url)

    return output
