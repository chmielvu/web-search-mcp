from __future__ import annotations

from typing import Any

from ...content.sitemap import SitemapConfig, crawl_and_extract_pages
from ...settings import settings


async def fetch_semantic_sitemap_payload(
    url: str,
    *,
    max_pages: int,
    max_depth: int,
    heading_preview_chars: int,
    generate_llms_txt: bool,
) -> dict[str, Any]:
    config = SitemapConfig(
        max_pages=max_pages,
        max_depth=max_depth,
        heading_preview_chars=heading_preview_chars,
        generate_llms_txt=generate_llms_txt,
        crawl_timeout_seconds=settings.crawl4ai_timeout_seconds,
        headless=settings.crawl4ai_headless,
    )
    return await crawl_and_extract_pages(url, config=config)
