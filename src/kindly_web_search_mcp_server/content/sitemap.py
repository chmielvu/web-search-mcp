"""Semantic sitemap generation using Crawl4AI.

Provides URL discovery (via sitemap XML, Common Crawl index, or seed crawl),
content extraction with heading-based section structuring, and optional
llms.txt markdown generation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from crawl4ai import (
    AsyncUrlSeeder,
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    DefaultMarkdownGenerator,
    PruningContentFilter,
)
from crawl4ai.async_configs import SeedingConfig

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


async def discover_urls(
    url: str,
    *,
    config: SitemapConfig,
) -> list[dict[str, Any]]:
    """Discover URLs from sitemap/Common Crawl using AsyncUrlSeeder."""
    domain = _extract_domain(url)
    seeder = AsyncUrlSeeder()
    try:
        seeding_config = SeedingConfig(
            source="sitemap",
            extract_head=True,
            max_urls=config.max_pages,
            filter_nonsense_urls=True,
        )
        urls = await seeder.urls(domain, seeding_config)
        if urls:
            return urls

        LOGGER.info("Sitemap returned 0 URLs for %s, trying Common Crawl", domain)
        seeding_config = SeedingConfig(
            source="cc",
            max_urls=config.max_pages,
            filter_nonsense_urls=True,
        )
        urls = await seeder.urls(domain, seeding_config)
        return urls
    except Exception as exc:
        LOGGER.warning("URL seeder failed for %s: %s", domain, exc)
        return []
    finally:
        await seeder.close()


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
    browser_config = BrowserConfig(headless=config.headless)
    md_generator = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(),
    )
    run_config = CrawlerRunConfig(
        markdown_generator=md_generator,
        page_timeout=int(config.crawl_timeout_seconds * 1000),
        cache_mode=CacheMode.ENABLED,
        exclude_external_links=config.include_external is False,
        verbose=False,
    )

    pages: list[SitemapPage] = []
    stats: dict[str, int] = {
        "pages_crawled": 0,
        "pages_failed": 0,
        "total_sections": 0,
    }

    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await crawler.arun_many(
            urls=urls_to_crawl,
            config=run_config,
        )
        for result in results:
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
