"""Specialized resolver orchestration for Tier 1 of the content pipeline.

Provides ``_resolve_tier1`` which tries all specialized resolvers in order
(StackExchange, GitHub Issues, GitHub Discussions, Wikipedia, arXiv, Telegram).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from ..errors import classify_error
from ..utils.url_canonicalize import canonicalize_url
from ..telemetry import record_content_error, record_content_resolution
from .artifact import ContentArtifact, ContentError
from .options import FetchOptions
from .resolvers.arxiv import ArxivError, fetch_arxiv_paper_markdown, parse_arxiv_url
from .resolvers.github_discussions import (
    fetch_github_discussion_thread_markdown,
    parse_github_discussion_url,
)
from .resolvers.github_issues import fetch_github_issue_thread_markdown, parse_github_issue_url
from .resolvers.stackexchange import fetch_stackexchange_thread_markdown, parse_stackexchange_url
from .resolvers.telegram import TelegramContentError, fetch_telegram_markdown, parse_telegram_url
from .resolvers.wikipedia import fetch_wikipedia_article_markdown, parse_wikipedia_url
from .status_classifier import classify_markdown

LOGGER = logging.getLogger(__name__)


def _to_content_error(exc: Exception, code: str, provider: str | None = None) -> ContentError:
    """Convert any exception to a ContentError with error_type and retryable flag."""
    structured = classify_error(exc, provider=provider)
    retryable = structured.error_type in ("rate_limit", "network")
    return ContentError(code=code, message=structured.error or str(exc), retryable=retryable)


async def _maybe_specialized(
    url: str,
    *,
    parser: Callable[[str], str],
    fetcher: Callable[[str], Awaitable[str]],
    source_type: str,
) -> ContentArtifact | None:
    """Try a specialized resolver. Returns None if the URL doesn't match."""
    try:
        parser(url)
    except Exception:
        return None

    try:
        markdown = await fetcher(url)
    except Exception as exc:
        record_content_resolution(stage=source_type, url=url, success=False, duration_seconds=None)
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
            error=_to_content_error(exc, code=f"{source_type}_fetch_failed", provider=source_type),
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
        else ContentError(code=cls.reason or "partial", message=cls.reason or "partial"),
    )


async def _resolve_tier1(url: str, options: FetchOptions) -> ContentArtifact | None:
    """Try all specialized resolvers in order. Returns an artifact or None if no resolver matches."""
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

    # arXiv (special handling for PDF)
    try:
        parse_arxiv_url(url)
    except ArxivError:
        pass
    else:
        try:
            arxiv_md = await fetch_arxiv_paper_markdown(url)
            return ContentArtifact(
                input_url=url,
                normalized_url=canonicalize_url(url),
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
            record_content_error(stage="arxiv", url=url, error_type="arxiv_fetch_failed")
            return ContentArtifact(
                input_url=url,
                normalized_url=canonicalize_url(url),
                fetched_url=url,
                status="error",
                source_type="arxiv",
                fetch_backend="arxiv_api_pdf",
                content_type=None,
                markdown="",
                word_count=0,
                quality_score=0.0,
                error=_to_content_error(exc, code="arxiv_fetch_failed", provider="arxiv"),
            )

    # Telegram (t.me URLs)
    try:
        parse_telegram_url(url)
    except TelegramContentError:
        return None
    else:
        try:
            tg_md = await fetch_telegram_markdown(url)
            cls = classify_markdown(tg_md)
            record_content_resolution(
                stage="telegram",
                url=url,
                success=cls.status == "success",
                size_bytes=len(tg_md.encode("utf-8")),
                word_count=len(tg_md.split()),
                extraction_method="telethon_mtproto",
            )
            return ContentArtifact(
                input_url=url,
                normalized_url=canonicalize_url(url),
                fetched_url=url,
                status=cls.status,
                source_type="telegram",
                fetch_backend="telethon_mtproto",
                content_type="text/markdown",
                markdown=tg_md,
                word_count=len(tg_md.split()),
                quality_score=1.0 if cls.status == "success" else 0.4,
                error=None
                if cls.status == "success"
                else ContentError(code="telegram_partial", message="partial content"),
            )
        except Exception as exc:
            record_content_resolution(
                stage="telegram", url=url, success=False, extraction_method="telethon_mtproto"
            )
            return ContentArtifact(
                input_url=url,
                normalized_url=canonicalize_url(url),
                fetched_url=url,
                status="error",
                source_type="telegram",
                fetch_backend="telethon_mtproto",
                content_type=None,
                markdown="",
                word_count=0,
                quality_score=0.0,
                error=_to_content_error(exc, code="telegram_fetch_failed", provider="telegram"),
            )
