"""Tier-2 generic extraction stages — one directly-callable async function per backend.

No orchestration here; fetch_pipeline.py owns availability ordering.

Stages:
  - _fetch_via_jina      : Jina Reader (free, no API key)
  - _fetch_via_local     : BS4+markdownify (offline, pure HTTP)
  - _fetch_via_crawl4ai  : Crawl4AI remote POST /md (cloud markdown)
  - _fetch_via_camoufox  : Camoufox sidecar (stealth-Firefox, returns raw HTML -> markdown)
"""

from __future__ import annotations

import logging
import os
import httpx
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
from .remote_clients import (
    CamoufoxClientError,
    Crawl4AIClientError,
    get_camoufox_client,
    get_crawl4ai_client,
)
from .safe_fetch import SafeFetchError, safe_fetch_url
from .status_classifier import classify_markdown
from ..search.normalize import canonicalize_url
from ..telemetry import record_content_resolution

LOGGER = logging.getLogger(__name__)


def _render_pdf_markdown(pdf_bytes: bytes, source_url: str) -> str | None:
    """Best-effort PDF -> Markdown conversion."""
    try:
        from .resolvers.arxiv import _pdf_bytes_to_markdown_best_effort

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


# ------------------------------------------------------------------
# Stage 1: Jina Reader
# ------------------------------------------------------------------


async def _fetch_via_jina(url: str, *, options: FetchOptions) -> ContentArtifact | None:
    """Fetch via Jina Reader (free, no API key).

    Returns ``None`` on transport failure (unavailable). Returns a
    ``ContentArtifact`` on success or low-quality response.
    """
    try:
        jina_markdown = await fetch_with_jina_reader(url)
    except (JinaReaderError, httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
        LOGGER.debug("Jina Reader failed: %s", exc)
        return None

    cls = classify_markdown(jina_markdown)
    record_content_resolution(
        stage="jina_reader",
        url=url,
        success=cls.status == "success",
        size_bytes=len(jina_markdown.encode("utf-8")),
        word_count=len(jina_markdown.split()),
        extraction_method="jina_reader",
    )
    return ContentArtifact(
        input_url=url,
        normalized_url=canonicalize_url(url),
        fetched_url=url,
        status=cls.status,
        source_type="html",
        fetch_backend="jina_reader",
        content_type="text/markdown",
        markdown=jina_markdown,
        word_count=len(jina_markdown.split()),
        quality_score=0.9 if cls.status == "success" else 0.5,
        error=None
        if cls.status == "success"
        else ContentError(code=cls.reason or "jina_low_quality", message=cls.reason or "jina_low_quality"),
    )


# ------------------------------------------------------------------
# Stage 2: Local extraction (BS4+markdownify)
# ------------------------------------------------------------------


async def _fetch_via_local(url: str, *, options: FetchOptions) -> ContentArtifact:
    """Fetch via local BS4+markdownify (offline, pure HTTP).

    Always returns a ``ContentArtifact`` (never raises).
    """
    opts = options
    canonical = canonicalize_url(url)

    try:
        fetched = await safe_fetch_url(url)
    except SafeFetchError as exc:
        return ContentArtifact(
            input_url=url, normalized_url=canonical, fetched_url=None,
            status="blocked" if exc.code.startswith("private") else "error",
            source_type="web", fetch_backend="safe_http",
            content_type=None, markdown="", word_count=0, quality_score=0.0,
            error=ContentError(code=exc.code, message=str(exc), retryable=False),
        )
    except Exception as exc:
        return ContentArtifact(
            input_url=url, normalized_url=canonical, fetched_url=None,
            status="error", source_type="web", fetch_backend="fallback_failed",
            content_type=None, markdown="", word_count=0, quality_score=0.0,
            error=ContentError(code="fallback_fetch_failed", message=str(exc), retryable=True),
        )

    # Handle PDFs
    if fetched.is_pdf:
        pdf_markdown = _render_pdf_markdown(fetched.body, fetched.fetched_url)
        if pdf_markdown:
            return ContentArtifact(
                input_url=url, normalized_url=canonical, fetched_url=fetched.fetched_url,
                status="success", source_type="pdf", fetch_backend="pdf_extract",
                content_type=fetched.content_type, markdown=pdf_markdown,
                word_count=len(pdf_markdown.split()), quality_score=1.0,
            )

    html = fetched.text
    if opts.strip_selectors:
        html = strip_html_selectors(html, opts.strip_selectors)

    metadata: dict[str, Any] | None = None
    if opts.include_metadata:
        metadata = extract_html_metadata(html, page_url=url, fetched_url=fetched.fetched_url)

    links: list[dict[str, Any]] | None = None
    if opts.include_links:
        links = extract_html_links(
            html, base_url=fetched.fetched_url or url,
            max_links=opts.max_links, include_external=True, same_domain_only=False,
        )

    markdown = extract_content_as_markdown(html, url=fetched.fetched_url)
    cls = classify_markdown(markdown)

    return ContentArtifact(
        input_url=url, normalized_url=canonical, fetched_url=fetched.fetched_url,
        status=cls.status, source_type="html", fetch_backend="local",
        content_type=fetched.content_type, markdown=markdown,
        metadata=metadata, links=links,
        word_count=len(markdown.split()),
        quality_score=1.0 if cls.status == "success" else 0.4,
        error=None
        if cls.status == "success"
        else ContentError(code=cls.reason or "partial", message=cls.reason or "partial"),
    )


# ------------------------------------------------------------------
# Stage 3: Crawl4AI remote (POST /md, non-browser)
# ------------------------------------------------------------------


async def _fetch_via_crawl4ai(url: str, options: FetchOptions) -> ContentArtifact:
    """Fetch via Crawl4AI remote POST /md (non-browser cloud markdown).

    Raises ``Crawl4AIClientError`` on transport failure (unavailable).
    Returns a ``ContentArtifact`` on success or low-quality response.
    """
    client = get_crawl4ai_client()
    if client is None:
        raise Crawl4AIClientError("Crawl4AI client not configured", retryable=False)

    markdown = await client.fetch_markdown(url, mode="fit")
    cls = classify_markdown(markdown)
    record_content_resolution(
        stage="crawl4ai_remote", url=url, success=cls.status == "success",
        size_bytes=len(markdown.encode("utf-8")), word_count=len(markdown.split()),
        extraction_method="crawl4ai_md",
    )
    return ContentArtifact(
        input_url=url, normalized_url=canonicalize_url(url), fetched_url=url,
        status=cls.status, source_type="html", fetch_backend="crawl4ai_remote",
        content_type="text/markdown", markdown=markdown, metadata=None, links=None,
        word_count=len(markdown.split()),
        quality_score=1.0 if cls.status == "success" else 0.6,
        error=None if cls.status == "success" else ContentError(
            code=cls.reason or "crawl4ai_low_quality", message=cls.reason or "crawl4ai_low_quality"),
    )


# ------------------------------------------------------------------
# Stage 4: Camoufox sidecar (last-resort browser)
# ------------------------------------------------------------------


async def _fetch_via_camoufox(url: str, options: FetchOptions) -> ContentArtifact:
    """Fetch via Camoufox sidecar: raw HTML -> markdown + metadata + links.

    Camoufox returns raw HTML (POST /content), NOT markdown — pipe through
    extract_content_as_markdown. Do NOT swap for Crawl4AIClient.fetch_markdown.

    Raises ``CamoufoxClientError`` on transport failure (unavailable).
    Returns a ``ContentArtifact`` on success or low-quality response.
    """
    client = get_camoufox_client()
    if client is None:
        raise CamoufoxClientError("Camoufox client not configured", retryable=False)

    html = await client.fetch_html(url)  # retries 503 once internally
    if options.strip_selectors:
        html = strip_html_selectors(html, options.strip_selectors)

    markdown = extract_content_as_markdown(html, url=url)
    cls = classify_markdown(markdown)

    metadata = extract_html_metadata(html, page_url=url) if options.include_metadata else None
    links = extract_html_links(html, base_url=url, max_links=options.max_links) if options.include_links else None

    record_content_resolution(
        stage="camoufox_remote", url=url, success=cls.status == "success",
        size_bytes=len(markdown.encode("utf-8")), word_count=len(markdown.split()),
        extraction_method="camoufox_remote",
    )
    return ContentArtifact(
        input_url=url, normalized_url=canonicalize_url(url), fetched_url=url,
        status=cls.status, source_type="html", fetch_backend="camoufox_remote",
        content_type="text/markdown", markdown=markdown, metadata=metadata, links=links,
        word_count=len(markdown.split()),
        quality_score=1.0 if cls.status == "success" else 0.4,
        error=None if cls.status == "success" else ContentError(
            code=cls.reason or "camoufox_low_quality", message=cls.reason or "camoufox_low_quality"),
    )
