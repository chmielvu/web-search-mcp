from __future__ import annotations

import os
from typing import Callable, Awaitable

from ..scrape.extract import extract_content_as_markdown
from ..scrape.universal_html import load_url_as_markdown
from ..search.normalize import canonicalize_url
from .arxiv import (
    ArxivError,
    _pdf_bytes_to_markdown_best_effort,
    fetch_arxiv_paper_markdown,
    parse_arxiv_url,
)
from .artifact import ContentArtifact, ContentError
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
            error=ContentError(code=f"{source_type}_fetch_failed", message=str(exc), retryable=True),
        )

    cls = classify_markdown(markdown)
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
        error=None if cls.status == "success" else ContentError(code=cls.reason or "partial", message=cls.reason or "partial"),
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


async def fetch_content_artifact(url: str) -> ContentArtifact:
    canonical = canonicalize_url(url)

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
                error=ContentError(code="arxiv_fetch_failed", message=str(exc), retryable=True),
            )

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
                else ContentError(code=jina_cls.reason or "jina_low_quality", message=jina_cls.reason or "jina_low_quality"),
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
            error=ContentError(code="http_fetch_failed", message=str(exc), retryable=True),
        )

    if fetched.is_pdf:
        try:
            markdown = _render_generic_pdf_markdown(fetched.body, fetched.fetched_url)
        except Exception as exc:
            return ContentArtifact(
                input_url=url,
                normalized_url=canonical,
                fetched_url=fetched.fetched_url,
                status="unsupported",
                source_type="pdf",
                fetch_backend="pdf_extract",
                content_type=fetched.content_type,
                markdown="",
                word_count=0,
                quality_score=0.0,
                error=ContentError(code="pdf_extract_failed", message=str(exc), retryable=False),
            )
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

    direct_markdown = extract_content_as_markdown(fetched.text)
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
                word_count=len(jina_markdown.split()),
                quality_score=0.9,
            )
    except Exception:
        pass

    browser_markdown = await load_url_as_markdown(fetched.fetched_url)
    if browser_markdown:
        browser_cls = classify_markdown(browser_markdown)
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=fetched.fetched_url,
            status=browser_cls.status,
            source_type="html",
            fetch_backend="browser_fallback",
            content_type="text/markdown",
            markdown=browser_markdown,
            word_count=len(browser_markdown.split()),
            quality_score=0.6 if browser_cls.status == "success" else 0.2,
            error=None
            if browser_cls.status == "success"
            else ContentError(code=browser_cls.reason or "browser_low_quality", message=browser_cls.reason or "browser_low_quality"),
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
        word_count=len(direct_markdown.split()),
        quality_score=0.25,
        error=ContentError(
            code=direct_cls.reason or "extract_low_quality",
            message=direct_cls.reason or "extract_low_quality",
            retryable=False,
        ),
    )
