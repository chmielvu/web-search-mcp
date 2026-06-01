from __future__ import annotations

import asyncio
import os
from functools import partial
from typing import Callable, Awaitable

import httpx

from ..errors import classify_error
from ..scrape.extract import extract_content_as_markdown
from ..scrape.html_tools import (
    extract_html_links,
    extract_html_metadata,
    strip_html_selectors,
)
from ..scrape.universal_html import load_url_as_markdown
from ..search.normalize import canonicalize_url
from .arxiv import (
    ArxivError,
    _pdf_bytes_to_markdown_best_effort,
    fetch_arxiv_paper_markdown,
    parse_arxiv_url,
)
from .artifact import ContentArtifact, ContentError
from .options import FetchOptions
from .github_discussions import (
    fetch_github_discussion_thread_markdown,
    parse_github_discussion_url,
)
from .github_issues import (
    fetch_github_issue_thread_markdown,
    parse_github_issue_url,
)
from .jina_reader import fetch_with_jina_reader
from .safe_fetch import SafeFetchError, safe_fetch_url
from .stackexchange import (
    fetch_stackexchange_thread_markdown,
    parse_stackexchange_url,
)
from .status_classifier import classify_markdown
from .wikipedia import (
    fetch_wikipedia_article_markdown,
    parse_wikipedia_url,
)

from ..telemetry import (
    record_content_resolution,
    record_content_fallback,
    record_content_error,
)
from opentelemetry import trace

_content_tracer = trace.get_tracer(
    "kindly_web_search_mcp_server.content.fetch_pipeline"
)


def _to_content_error(
    exc: Exception, code: str, provider: str | None = None
) -> ContentError:
    """Convert any exception to a ContentError with proper error_type and retryable flag.

    Delegates to errors.classify_error() for HTTP/network-aware classification,
    falling back to generic ContentError for unknown exceptions.
    """
    structured = classify_error(exc, provider=provider)
    # Determine retryability: rate_limit and server errors (5xx) are retryable
    retryable = structured.error_type in ("rate_limit", "network")
    return ContentError(
        code=code,
        message=structured.error or str(exc),
        retryable=retryable,
    )


async def _maybe_specialized(
    url: str,
    *,
    parser: Callable[[str], str],
    fetcher: Callable[[str], Awaitable[str]],
    source_type: str,
) -> ContentArtifact | None:
    try:
        parser(url)
    except Exception:
        return None

    try:
        markdown = await fetcher(url)
    except Exception as exc:
        record_content_resolution(
            stage=source_type,
            url=url,
            success=False,
            duration_seconds=None,
        )
        return ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=url,
            status="error",
            source_type=source_type,
            fetch_backend=f"{source_type}_api",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=_to_content_error(
                exc, code=f"{source_type}_fetch_failed", provider=source_type
            ),
        )

    cls = classify_markdown(markdown)
    record_content_resolution(
        stage=source_type,
        url=url,
        success=cls.status == "success",
        size_bytes=len(markdown.encode("utf-8")),
        word_count=len(markdown.split()),
        extraction_method=f"{source_type}_api",
    )
    return ContentArtifact(
        input_url=url,
        normalized_url=canonicalize_url(url),
        fetched_url=url,
        status=cls.status,
        source_type=source_type,
        fetch_backend=f"{source_type}_api",
        content_type="text/markdown",
        markdown=markdown,
        word_count=len(markdown.split()),
        quality_score=1.0 if cls.status == "success" else 0.4,
        error=None
        if cls.status == "success"
        else ContentError(
            code=cls.reason or "partial", message=cls.reason or "partial"
        ),
    )


def _render_generic_pdf_markdown(pdf_bytes: bytes, source_url: str) -> str:
    max_pages = int((os.environ.get("KINDLY_GENERIC_PDF_MAX_PAGES") or "20").strip())
    rendered = _pdf_bytes_to_markdown_best_effort(pdf_bytes, max_pages=max_pages)
    return (
        "# PDF Document\n\n"
        f"Source: {source_url}\n\n"
        f"_Pages extracted: {rendered.pages_rendered}/{rendered.page_count}_\n\n"
        f"{rendered.markdown}".strip()
    )


async def fetch_content_artifact(
    url: str,
    *,
    fetch_options: FetchOptions | None = None,
) -> ContentArtifact:
    with _content_tracer.start_as_current_span("content.fetch_pipeline") as span:
        span.set_attribute("content.url", url)

        canonical = canonicalize_url(url)
        options = fetch_options or FetchOptions()
        options.validate()

        specialized = await _maybe_specialized(
            url,
            parser=parse_stackexchange_url,
            fetcher=fetch_stackexchange_thread_markdown,
            source_type="stackexchange",
        )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_github_issue_url,
        fetcher=fetch_github_issue_thread_markdown,
        source_type="github_issue",
    )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_github_discussion_url,
        fetcher=fetch_github_discussion_thread_markdown,
        source_type="github_discussion",
    )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_wikipedia_url,
        fetcher=fetch_wikipedia_article_markdown,
        source_type="wikipedia",
    )
    if specialized is not None:
        return specialized

    try:
        parse_arxiv_url(url)
    except ArxivError:
        pass
    else:
        try:
            arxiv_md = await fetch_arxiv_paper_markdown(url)
            return ContentArtifact(
                input_url=url,
                normalized_url=canonical,
                fetched_url=url,
                status="success",
                source_type="arxiv",
                fetch_backend="arxiv_api_pdf",
                content_type="text/markdown",
                markdown=arxiv_md,
                word_count=len(arxiv_md.split()),
                quality_score=1.0,
            )
        except Exception as exc:
            record_content_error(
                stage="arxiv", url=url, error_type="arxiv_fetch_failed"
            )
            return ContentArtifact(
                input_url=url,
                normalized_url=canonical,
                fetched_url=url,
                status="error",
                source_type="arxiv",
                fetch_backend="arxiv_api_pdf",
                content_type=None,
                markdown="",
                word_count=0,
                quality_score=0.0,
                error=_to_content_error(
                    exc, code="arxiv_fetch_failed", provider="arxiv"
                ),
            )

    try:
        fetched = await safe_fetch_url(url)
        record_content_resolution(
            stage="safe_http",
            url=url,
            success=True,
            size_bytes=len(fetched.body) if fetched else 0,
            extraction_method="trafilatura_safe",
        )
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
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        error_code = f"http_{status_code}"
        retryable = status_code >= 500 or status_code == 429
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=None,
            status="error",
            source_type="web",
            fetch_backend="safe_http",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(
                code=error_code,
                message=f"HTTP {status_code}: {str(exc)[:100]}",
                retryable=retryable,
            ),
        )
    except Exception as exc:
        record_content_fallback(stage="jina_reader", url=url, from_stage="safe_http")
        try:
            jina_markdown = await fetch_with_jina_reader(url)
            jina_cls = classify_markdown(jina_markdown)
            return ContentArtifact(
                input_url=url,
                normalized_url=canonical,
                fetched_url=None,
                status=jina_cls.status,
                source_type="html",
                fetch_backend="jina_reader",
                content_type="text/markdown",
                markdown=jina_markdown,
                word_count=len(jina_markdown.split()),
                quality_score=0.9 if jina_cls.status == "success" else 0.3,
                error=None
                if jina_cls.status == "success"
                else ContentError(
                    code=jina_cls.reason or "jina_low_quality",
                    message=jina_cls.reason or "jina_low_quality",
                ),
            )
        except Exception:
            pass
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=None,
            status="error",
            source_type="web",
            fetch_backend="safe_http",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=_to_content_error(exc, code="http_fetch_failed"),
        )

    if fetched.is_pdf:
        try:
            markdown = _render_generic_pdf_markdown(fetched.body, fetched.fetched_url)
        except Exception:
            markdown = None
        if markdown:
            return ContentArtifact(
                input_url=url,
                normalized_url=canonical,
                fetched_url=fetched.fetched_url,
                status="success",
                source_type="pdf",
                fetch_backend="pdf_extract",
                content_type=fetched.content_type,
                markdown=markdown,
                word_count=len(markdown.split()),
                quality_score=1.0,
            )
        # PDF extraction failed — fall through to Jina/browser stages
        # which may handle the PDF URL directly

    html = fetched.text
    if options.strip_selectors:
        html = strip_html_selectors(html, options.strip_selectors)

    metadata = (
        extract_html_metadata(html, page_url=url, fetched_url=fetched.fetched_url)
        if options.include_metadata
        else None
    )
    links = (
        extract_html_links(
            html,
            base_url=fetched.fetched_url or url,
            max_links=options.max_links,
            include_external=True,
            same_domain_only=False,
        )
        if options.include_links
        else None
    )

    direct_markdown = await asyncio.to_thread(
        partial(extract_content_as_markdown, html, url=fetched.fetched_url)
    )
    direct_cls = classify_markdown(direct_markdown)
    if direct_cls.status == "success":
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=fetched.fetched_url,
            status="success",
            source_type="html",
            fetch_backend="safe_http_extract",
            content_type=fetched.content_type,
            markdown=direct_markdown,
            metadata=metadata,
            links=links,
            word_count=len(direct_markdown.split()),
            quality_score=0.85,
        )

    try:
        jina_markdown = await fetch_with_jina_reader(fetched.fetched_url)
        jina_cls = classify_markdown(jina_markdown)
        if jina_cls.status == "success":
            return ContentArtifact(
                input_url=url,
                normalized_url=canonical,
                fetched_url=fetched.fetched_url,
                status="success",
                source_type="html",
                fetch_backend="jina_reader",
                content_type="text/markdown",
                markdown=jina_markdown,
                metadata=metadata,
                links=links,
                word_count=len(jina_markdown.split()),
                quality_score=0.9,
            )
    except Exception:
        pass

    browser_markdown = await load_url_as_markdown(fetched.fetched_url)
    if browser_markdown:
        browser_cls = classify_markdown(browser_markdown)
        record_content_resolution(
            stage="browser_nodriver",
            url=url,
            success=browser_cls.status == "success",
            size_bytes=len(browser_markdown.encode("utf-8")),
            word_count=len(browser_markdown.split()),
            extraction_method="nodriver",
        )
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=fetched.fetched_url,
            status=browser_cls.status,
            source_type="html",
            fetch_backend="browser_fallback",
            content_type="text/markdown",
            markdown=browser_markdown,
            metadata=metadata,
            links=links,
            word_count=len(browser_markdown.split()),
            quality_score=0.6 if browser_cls.status == "success" else 0.2,
            error=None
            if browser_cls.status == "success"
            else ContentError(
                code=browser_cls.reason or "browser_low_quality",
                message=browser_cls.reason or "browser_low_quality",
            ),
        )

    return ContentArtifact(
        input_url=url,
        normalized_url=canonical,
        fetched_url=fetched.fetched_url,
        status=direct_cls.status,
        source_type="html",
        fetch_backend="safe_http_extract",
        content_type=fetched.content_type,
        markdown=direct_markdown,
        metadata=metadata,
        links=links,
        word_count=len(direct_markdown.split()),
        quality_score=0.25,
        error=ContentError(
            code=direct_cls.reason or "extract_low_quality",
            message=direct_cls.reason or "extract_low_quality",
            retryable=False,
        ),
    )
