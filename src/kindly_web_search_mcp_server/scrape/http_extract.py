"""HTTP-based content extraction using trafilatura as primary method.

This module provides lightweight extraction without browser dependency.
Priority: trafilatura (primary) → html2text (fallback) → nodriver (last resort).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .extract import extract_content_as_markdown, _bs4_markdownify_fallback

try:
    import trafilatura  # type: ignore
except Exception:
    trafilatura = None  # type: ignore

try:
    import html2text  # type: ignore
except Exception:
    html2text = None  # type: ignore

LOGGER = logging.getLogger(__name__)

# Minimum word count for valid extraction (filter low-quality results)
MIN_WORD_COUNT = 50


@dataclass
class HttpExtractResult:
    """Result from HTTP-based extraction."""
    url: str
    text: str
    title: str | None = None
    method: str = "unknown"  # "trafilatura", "html2text", "bs4", "raw"
    word_count: int = 0
    metadata: dict[str, Any] | None = None
    error: str | None = None


async def fetch_html(url: str, timeout: float = 15.0) -> tuple[str | None, str | None]:
    """Fetch HTML content via HTTP.

    Returns: (html, error) tuple.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text, None
        except httpx.TimeoutException:
            return None, f"Timeout fetching {url}"
        except httpx.RequestError as e:
            return None, f"Request error: {e}"
        except httpx.HTTPStatusError as e:
            return None, f"HTTP {e.response.status_code}"


def _count_words(text: str) -> int:
    """Count words in text."""
    if not text:
        return 0
    return len(text.split())


def _extract_metadata(html: str) -> dict[str, Any]:
    """Extract metadata from HTML using trafilatura."""
    if trafilatura is None:
        return {}
    try:
        meta = trafilatura.extract_metadata(html)
        if meta:
            return {
                "title": meta.title,
                "author": meta.author,
                "date": meta.date,
                "description": meta.description,
                "sitename": meta.sitename,
            }
    except Exception:
        pass
    return {}


async def extract_with_trafilatura(html: str, url: str) -> HttpExtractResult | None:
    """Extract content using trafilatura (primary method)."""
    if trafilatura is None:
        return None

    try:
        text = await asyncio.to_thread(
            trafilatura.extract,
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
            url=url,
        )
        if not text:
            return None

        word_count = _count_words(text)
        if word_count < MIN_WORD_COUNT:
            LOGGER.debug(f"Trafilatura result too short: {word_count} words for {url}")
            return None

        metadata = _extract_metadata(html)
        return HttpExtractResult(
            url=url,
            text=text,
            title=metadata.get("title"),
            method="trafilatura",
            word_count=word_count,
            metadata=metadata,
        )
    except Exception as e:
        LOGGER.warning(f"Trafilatura extraction failed for {url}: {e}")
        return None


async def extract_with_html2text(html: str, url: str) -> HttpExtractResult | None:
    """Extract content using html2text (fallback method)."""
    if html2text is None:
        return None

    try:
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = True  # Skip images for cleaner output
        converter.ignore_emphasis = False
        converter.body_width = 0  # No line wrapping

        text = await asyncio.to_thread(converter.handle, html)
        if not text:
            return None

        word_count = _count_words(text)
        if word_count < MIN_WORD_COUNT:
            LOGGER.debug(f"html2text result too short: {word_count} words for {url}")
            return None

        return HttpExtractResult(
            url=url,
            text=text,
            title=None,
            method="html2text",
            word_count=word_count,
        )
    except Exception as e:
        LOGGER.warning(f"html2text extraction failed for {url}: {e}")
        return None


async def extract_with_bs4(html: str, url: str) -> HttpExtractResult | None:
    """Extract content using BeautifulSoup + markdownify (fallback)."""
    try:
        text = await asyncio.to_thread(_bs4_markdownify_fallback, html)
        if not text or text == "Could not extract main content.":
            return None

        word_count = _count_words(text)
        if word_count < MIN_WORD_COUNT:
            LOGGER.debug(f"BS4 result too short: {word_count} words for {url}")
            return None

        return HttpExtractResult(
            url=url,
            text=text,
            title=None,
            method="bs4",
            word_count=word_count,
        )
    except Exception as e:
        LOGGER.warning(f"BS4 extraction failed for {url}: {e}")
        return None


async def http_extract(url: str, timeout: float = 15.0) -> HttpExtractResult:
    """Extract content from URL via HTTP (no browser).

    Priority: trafilatura → html2text → bs4 → raw fetch.

    This is the PRIMARY extraction method for web-search-mcp.
    Browser-based extraction (nodriver) should only be used as fallback
    for JavaScript-heavy sites.
    """
    # 1. Fetch HTML
    html, error = await fetch_html(url, timeout)
    if error:
        return HttpExtractResult(url=url, text="", error=error)

    if not html:
        return HttpExtractResult(url=url, text="", error="No HTML content")

    # 2. Try trafilatura first (best quality)
    result = await extract_with_trafilatura(html, url)
    if result:
        LOGGER.info(f"Extracted {url} via trafilatura: {result.word_count} words")
        return result

    # 3. Fallback to html2text
    result = await extract_with_html2text(html, url)
    if result:
        LOGGER.info(f"Extracted {url} via html2text: {result.word_count} words")
        return result

    # 4. Fallback to BS4/markdownify
    result = await extract_with_bs4(html, url)
    if result:
        LOGGER.info(f"Extracted {url} via bs4: {result.word_count} words")
        return result

    # 5. Last resort: use existing extract_content_as_markdown
    text = await asyncio.to_thread(extract_content_as_markdown, html)
    word_count = _count_words(text)

    return HttpExtractResult(
        url=url,
        text=text,
        title=None,
        method="fallback",
        word_count=word_count,
    )


async def http_extract_batch(urls: list[str], timeout: float = 15.0, max_concurrent: int = 5) -> list[HttpExtractResult]:
    """Extract content from multiple URLs concurrently.

    Uses semaphore to limit concurrent requests.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def extract_with_limit(url: str) -> HttpExtractResult:
        async with semaphore:
            return await http_extract(url, timeout)

    return await asyncio.gather(*[extract_with_limit(u) for u in urls])