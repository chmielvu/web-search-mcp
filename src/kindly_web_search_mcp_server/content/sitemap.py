"""Semantic sitemap generation using Crawl4AI remote deep crawl.

Uses BestFirstCrawlingStrategy for intelligent URL discovery and crawling.
Extracts heading-based section hierarchy (H1-H6) from each page's markdown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .crawl4ai_client import get_crawl4ai_client


@dataclass(frozen=True)
class SitemapConfig:
    """Configuration for semantic sitemap generation."""

    max_pages: int = 100
    max_depth: int = 3
    include_external: bool = False
    heading_preview_chars: int = 200
    generate_llms_txt: bool = False
    keywords: list[str] | None = None


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
# Main crawl entry point
# ---------------------------------------------------------------------------


async def crawl_and_extract_pages(
    url: str,
    *,
    config: SitemapConfig,
) -> dict[str, Any]:
    """Crawl a site via Crawl4AI deep crawl and extract heading sections.

    Uses BestFirstCrawlingStrategy for intelligent page discovery and
    prioritization. When keywords are provided, pages are scored by
    keyword relevance; otherwise, path depth scoring is used.

    Returns a dict with keys: query_url, pages, stats, and optionally
    llms_txt_markdown.
    """
    client = get_crawl4ai_client()
    if client is None:
        raise RuntimeError(
            "CRAWL4AI_BASE_URL not configured. The sitemap tool requires a remote Crawl4AI server."
        )

    # Phase 1: Deep crawl via Crawl4AI
    results = await client.deep_crawl(
        url,
        max_depth=config.max_depth,
        max_pages=config.max_pages,
        keywords=config.keywords,
    )

    # Phase 2: Extract headings from each result's markdown
    pages: list[SitemapPage] = []
    stats: dict[str, int] = {
        "pages_crawled": 0,
        "pages_failed": 0,
        "total_sections": 0,
    }

    for result in results:
        if not result.get("success"):
            stats["pages_failed"] += 1
            continue
        stats["pages_crawled"] += 1

        md_data = result.get("markdown", {})
        if isinstance(md_data, dict):
            md = md_data.get("fit_markdown") or md_data.get("raw_markdown") or ""
        elif isinstance(md_data, str):
            md = md_data
        else:
            md = ""

        sections = _extract_sections_from_markdown(md, preview_chars=config.heading_preview_chars)
        title = _extract_title_from_markdown(md)
        stats["total_sections"] += len(sections)

        pages.append(
            SitemapPage(
                url=result.get("url", ""),
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
