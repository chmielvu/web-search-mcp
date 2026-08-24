"""Specialized resolver orchestration for Tier 1 of the content pipeline.

Provides ``_resolve_tier1`` which tries specialized resolvers in priority order:
- Raw text & source code files
- Multi-format documents (PDF, DOCX, PPTX, XLSX, EPUB, IPYNB, CSV)
- Academic DOIs & Open Access papers (Unpaywall / Crossref)
- Package registries (PyPI, npm, Hugging Face, Crates.io)
- Developer & Q&A platforms (StackExchange, GitHub, Discourse, HackerNews, Reddit, Wikipedia, arXiv, YouTube, Telegram)
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from ..errors import classify_error
from ..utils.url_canonicalize import canonicalize_url
from ..telemetry import record_content_error, record_content_resolution
from .artifact import ContentArtifact, ContentError
from .options import FetchOptions
from .resolvers.arxiv import ArxivError, fetch_arxiv_paper_markdown, parse_arxiv_url
from .resolvers.crates import fetch_crates_markdown, parse_crates_url
from .resolvers.discourse import fetch_discourse_topic_markdown, parse_discourse_url
from .resolvers.document import fetch_document_markdown, is_document_url
from .resolvers.github_discussions import (
    fetch_github_discussion_thread_markdown,
    parse_github_discussion_url,
)
from .resolvers.github_issues import fetch_github_issue_thread_markdown, parse_github_issue_url
from .resolvers.github_pulls import (
    fetch_github_pull_thread_markdown,
    parse_github_pull_url,
)
from .resolvers.github_repo import (
    fetch_github_repo_markdown,
    parse_github_repo_url,
)
from .resolvers.hackernews import (
    fetch_hackernews_thread_markdown,
    parse_hackernews_url,
)
from .resolvers.huggingface import fetch_huggingface_markdown, parse_huggingface_url
from .resolvers.npm import fetch_npm_package_markdown, parse_npm_url
from .resolvers.pypi import fetch_pypi_package_markdown, parse_pypi_url
from .resolvers.raw_text import fetch_raw_text_markdown, is_raw_text_url
from .resolvers.reddit import (
    fetch_reddit_thread_markdown,
    parse_reddit_url,
)
from .resolvers.stackexchange import fetch_stackexchange_thread_markdown, parse_stackexchange_url
from .resolvers.telegram import TelegramContentError, fetch_telegram_markdown, parse_telegram_url
from .resolvers.twitter import fetch_twitter_markdown, parse_twitter_url
from .resolvers.unpaywall import fetch_doi_paper_markdown, parse_doi_url
from .resolvers.wikipedia import fetch_wikipedia_article_markdown, parse_wikipedia_url
from .resolvers.youtube import (
    fetch_youtube_content_markdown,
    parse_youtube_content_url,
)
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
    parser: Callable[[str], Any],
    fetcher: Callable[[str], Awaitable[str]],
    source_type: str,
    allow_fallback: bool = True,
) -> ContentArtifact | None:
    """Try a specialized resolver. Returns None if URL doesn't match or on error when allow_fallback is True."""
    try:
        parsed = parser(url)
    except Exception:
        return None
    if parsed is None:
        return None

    try:
        markdown = await fetcher(url)
    except Exception as exc:
        record_content_resolution(stage=source_type, url=url, success=False, duration_seconds=None)
        LOGGER.debug("Specialized resolver %s failed for %s: %s", source_type, url, exc)
        if allow_fallback:
            return None
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
    if cls.status == "error" and allow_fallback:
        LOGGER.debug(
            "Specialized resolver %s returned error status for %s, falling back", source_type, url
        )
        return None

    word_count = len(markdown.split())
    is_success = cls.status == "success" or (
        cls.status == "partial" and cls.reason == "too_short" and word_count >= 25
    )
    status = "success" if is_success else cls.status

    record_content_resolution(
        stage=source_type,
        url=url,
        success=is_success,
        size_bytes=len(markdown.encode("utf-8")),
        word_count=word_count,
        extraction_method=f"{source_type}_api",
    )
    return ContentArtifact(
        input_url=url,
        normalized_url=canonicalize_url(url),
        fetched_url=url,
        status=status,
        source_type=source_type,
        fetch_backend=f"{source_type}_api",
        content_type="text/markdown",
        markdown=markdown,
        word_count=word_count,
        quality_score=1.0 if is_success else 0.4,
        error=None
        if is_success
        else ContentError(code=cls.reason or "partial", message=cls.reason or "partial"),
    )


async def _resolve_tier1(url: str, options: FetchOptions) -> ContentArtifact | None:
    """Try all specialized resolvers in order. Returns an artifact or None if no resolver matches."""
    # 1. Documents (PDF, DOCX, PPTX, XLSX, EPUB, IPYNB, CSV, Google Docs/Sheets)
    if is_document_url(url):
        doc_artifact = await fetch_document_markdown(url, fetch_options=options)
        if doc_artifact.status in ("success", "partial"):
            return doc_artifact

    # 2. Raw text & code files
    if is_raw_text_url(url):
        raw_artifact = await fetch_raw_text_markdown(url, fetch_options=options)
        if raw_artifact.status in ("success", "partial"):
            return raw_artifact

    # 3. Academic DOIs & Open Access papers (Unpaywall)
    if parse_doi_url(url) is not None:
        doi_artifact = await fetch_doi_paper_markdown(url, fetch_options=options)
        if doi_artifact.status in ("success", "partial"):
            return doi_artifact

    # 4. Package Registries (PyPI, npm, Hugging Face, Crates.io)
    specialized = await _maybe_specialized(
        url,
        parser=parse_pypi_url,
        fetcher=fetch_pypi_package_markdown,
        source_type="pypi_package",
    )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_npm_url,
        fetcher=fetch_npm_package_markdown,
        source_type="npm_package",
    )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_huggingface_url,
        fetcher=fetch_huggingface_markdown,
        source_type="huggingface_hub",
    )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_crates_url,
        fetcher=fetch_crates_markdown,
        source_type="crates_io",
    )
    if specialized is not None:
        return specialized

    # 5. Developer & Community Forums (Discourse, StackExchange, GitHub)
    specialized = await _maybe_specialized(
        url,
        parser=parse_discourse_url,
        fetcher=fetch_discourse_topic_markdown,
        source_type="discourse_topic",
    )
    if specialized is not None:
        return specialized

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
        parser=parse_github_pull_url,
        fetcher=fetch_github_pull_thread_markdown,
        source_type="github_pull",
    )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_github_repo_url,
        fetcher=fetch_github_repo_markdown,
        source_type="github_repo",
    )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_hackernews_url,
        fetcher=fetch_hackernews_thread_markdown,
        source_type="hackernews",
    )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_reddit_url,
        fetcher=fetch_reddit_thread_markdown,
        source_type="reddit",
    )
    if specialized is not None:
        return specialized

    # X/Twitter (Apify-backed; inert unless APIFY_API_TOKEN is configured)
    specialized = await _maybe_specialized(
        url,
        parser=parse_twitter_url,
        fetcher=fetch_twitter_markdown,
        source_type="twitter",
    )
    if specialized is not None:
        return specialized

    # YouTube (video URLs)
    specialized = await _maybe_specialized(
        url,
        parser=parse_youtube_content_url,
        fetcher=fetch_youtube_content_markdown,
        source_type="youtube",
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
        except Exception:
            record_content_error(stage="arxiv", url=url, error_type="arxiv_fetch_failed")
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
        except Exception:
            record_content_resolution(
                stage="telegram", url=url, success=False, extraction_method="telethon_mtproto"
            )
            return None
