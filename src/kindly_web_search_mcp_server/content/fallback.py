"""Consolidated fallback content extraction chain.

Used when Crawl4AI remote is unavailable or fails.
Pipeline: Jina Reader (free, no API key) → trafilatura (offline, pure HTTP).

Both backends are fast (~2-5s) and have no browser dependency.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .artifact import ContentArtifact, ContentError
from .extract import extract_content_as_markdown
from .html_tools import (
    extract_html_links,
    extract_html_metadata,
    strip_html_selectors,
)
from .jina_reader import JinaReaderError, fetch_with_jina_reader
from .options import FetchOptions
from .safe_fetch import SafeFetchError, safe_fetch_url
from .status_classifier import classify_markdown
from ..search.normalize import canonicalize_url

LOGGER = logging.getLogger(__name__)


def _render_pdf_markdown(pdf_bytes: bytes, source_url: str) -> str | None:
    """Best-effort PDF → Markdown conversion."""
    try:
        from .arxiv import _pdf_bytes_to_markdown_best_effort

        max_pages = int((os.environ.get("GENERIC_PDF_MAX_PAGES") or "20").strip())
        rendered = _pdf_bytes_to_markdown_best_effort(pdf_bytes, max_pages=max_pages)
        return (
            f"# PDF Document\n\n"
            f"Source: {source_url}\n\n"
            f"_Pages extracted: {rendered.pages_rendered}/{rendered.page_count}_\n\n"
            f"{rendered.markdown}".strip()
        )
    except Exception as exc:
        LOGGER.debug("PDF rendering failed for %s: %s", source_url, exc)
        return None


async def fallback_fetch_content(
    url: str,
    *,
    options: FetchOptions | None = None,
) -> ContentArtifact:
    """Fallback content extraction: Jina Reader → trafilatura.

    Used when Crawl4AI remote is unavailable or fails.

    Stage 1: Jina Reader — free tier (no API key), returns clean markdown.
    If Jina rate-limited (429) or fails → Stage 2.

    Stage 2: Trafilatura — offline, pure HTTP GET + local extraction.
    Handles PDFs, metadata, and link extraction from raw HTML.
    """
    opts = options or FetchOptions()
    canonical = canonicalize_url(url)

    # ------------------------------------------------------------------
    # Stage 1: Jina Reader (free, no API key required)
    # ------------------------------------------------------------------
    try:
        jina_markdown = await fetch_with_jina_reader(url)
        cls = classify_markdown(jina_markdown)
        if cls.status == "success":
            LOGGER.info("Fetched via Jina Reader: %d chars", len(jina_markdown))
            return ContentArtifact(
                input_url=url,
                normalized_url=canonical,
                fetched_url=url,
                status="success",
                source_type="html",
                fetch_backend="jina_reader",
                content_type="text/markdown",
                markdown=jina_markdown,
                word_count=len(jina_markdown.split()),
                quality_score=0.9,
            )
        # Jina returned content but quality is low — try trafilatura
        LOGGER.info("Jina Reader quality low (%s), trying trafilatura", cls.reason)
    except JinaReaderError as exc:
        LOGGER.debug("Jina Reader failed: %s", exc)
    except Exception as exc:
        LOGGER.debug("Jina Reader unexpected error: %s", exc)

    # ------------------------------------------------------------------
    # Stage 2: Trafilatura (offline, pure HTTP)
    # ------------------------------------------------------------------
    try:
        fetched = await safe_fetch_url(url)
    except SafeFetchError as exc:
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=None,
            status="blocked" if exc.code.startswith("private") else "error",
            source_type="web",
            fetch_backend="safe_http",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(code=exc.code, message=str(exc), retryable=False),
        )
    except Exception as exc:
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=None,
            status="error",
            source_type="web",
            fetch_backend="fallback_failed",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(
                code="fallback_fetch_failed",
                message=str(exc),
                retryable=True,
            ),
        )

    # Handle PDFs
    if fetched.is_pdf:
        pdf_markdown = _render_pdf_markdown(fetched.body, fetched.fetched_url)
        if pdf_markdown:
            return ContentArtifact(
                input_url=url,
                normalized_url=canonical,
                fetched_url=fetched.fetched_url,
                status="success",
                source_type="pdf",
                fetch_backend="pdf_extract",
                content_type=fetched.content_type,
                markdown=pdf_markdown,
                word_count=len(pdf_markdown.split()),
                quality_score=1.0,
            )

    # Extract metadata and links from raw HTML
    html = fetched.text
    if opts.strip_selectors:
        html = strip_html_selectors(html, opts.strip_selectors)

    metadata: dict[str, Any] | None = None
    if opts.include_metadata:
        metadata = extract_html_metadata(html, page_url=url, fetched_url=fetched.fetched_url)

    links: list[dict[str, Any]] | None = None
    if opts.include_links:
        links = extract_html_links(
            html,
            base_url=fetched.fetched_url or url,
            max_links=opts.max_links,
            include_external=True,
            same_domain_only=False,
        )

    # Trafilatura extraction
    markdown = extract_content_as_markdown(html, url=fetched.fetched_url)
    cls = classify_markdown(markdown)

    return ContentArtifact(
        input_url=url,
        normalized_url=canonical,
        fetched_url=fetched.fetched_url,
        status=cls.status,
        source_type="html",
        fetch_backend="trafilatura",
        content_type=fetched.content_type,
        markdown=markdown,
        metadata=metadata,
        links=links,
        word_count=len(markdown.split()),
        quality_score=1.0 if cls.status == "success" else 0.4,
        error=None
        if cls.status == "success"
        else ContentError(
            code=cls.reason or "partial",
            message=cls.reason or "partial",
        ),
    )
