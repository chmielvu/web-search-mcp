from __future__ import annotations

import logging
import time

from .stackexchange import (
    StackExchangeError,
    fetch_stackexchange_thread_markdown,
    parse_stackexchange_url,
)
from .github_issues import (
    GitHubIssueError,
    fetch_github_issue_thread_markdown,
    parse_github_issue_url,
)
from .github_discussions import (
    GitHubDiscussionError,
    fetch_github_discussion_thread_markdown,
    parse_github_discussion_url,
)
from .wikipedia import (
    WikipediaError,
    fetch_wikipedia_article_markdown,
    parse_wikipedia_url,
)
from .arxiv import (
    ArxivError,
    fetch_arxiv_paper_markdown,
    parse_arxiv_url,
)
from ..scrape.universal_html import load_url_as_markdown
from ..scrape.http_extract import http_extract
from ..utils.diagnostics import Diagnostics
from ..telemetry import record_content_resolution

LOGGER = logging.getLogger(__name__)


async def resolve_page_content_markdown(
    url: str,
    *,
    diagnostics: Diagnostics | None = None,
) -> str | None:
    """Resolve a URL to LLM-ready Markdown if supported.

    Stage 1: StackExchange API (StackOverflow + stackexchange network).
    Stage 2: GitHub Issue API (GitHub GraphQL).
    Stage 3: GitHub Discussions API (GitHub GraphQL).
    Stage 4: Wikipedia API (MediaWiki Action API).
    Stage 5: arXiv (Atom API + PDF → Markdown).
    Stage 6: HTTP extraction (trafilatura primary, no browser).
    Stage 7: Universal HTML loader fallback (headless Nodriver for JS sites).
    """
    start_time = time.time()

    if diagnostics:
        diagnostics.emit("resolver.start", "Resolving URL", {"url": url})

    try:
        # Validate we can parse as StackExchange first.
        parse_stackexchange_url(url)
    except StackExchangeError:
        pass
    else:
        if diagnostics:
            diagnostics.emit("resolver.route", "Matched StackExchange URL", {"handler": "stackexchange"})
        handler_start = time.time()
        try:
            result = await fetch_stackexchange_thread_markdown(url)
            word_count = len(result.split()) if result else 0
            record_content_resolution(
                stage="stackexchange",
                url=url,
                success=True,
                duration_seconds=time.time() - handler_start,
                word_count=word_count,
                extraction_method="api",
            )
            return result
        except Exception as e:
            record_content_resolution(
                stage="stackexchange",
                url=url,
                success=False,
                duration_seconds=time.time() - handler_start,
            )
            if diagnostics:
                diagnostics.emit(
                    "resolver.error",
                    "StackExchange handler failed",
                    {"handler": "stackexchange", "error": type(e).__name__},
                )
            # Best-effort: return a short Markdown error note (no secrets).
            return f"_Failed to retrieve StackExchange content: {type(e).__name__}_\n\nSource: {url}\n"

    try:
        parse_github_issue_url(url)
    except GitHubIssueError:
        pass
    else:
        if diagnostics:
            diagnostics.emit("resolver.route", "Matched GitHub Issue URL", {"handler": "github_issue"})
        handler_start = time.time()
        try:
            result = await fetch_github_issue_thread_markdown(url)
            word_count = len(result.split()) if result else 0
            record_content_resolution(
                stage="github_issue",
                url=url,
                success=True,
                duration_seconds=time.time() - handler_start,
                word_count=word_count,
                extraction_method="graphql",
            )
            return result
        except Exception:
            record_content_resolution(
                stage="github_issue",
                url=url,
                success=False,
                duration_seconds=time.time() - handler_start,
            )
            if diagnostics:
                diagnostics.emit(
                    "resolver.fallback",
                    "GitHub Issue handler failed; falling back to HTML",
                    {"handler": "github_issue"},
                )
            # Prefer falling back to HTML loader for resilience (e.g., missing token, rate-limit).
            fallback = await load_url_as_markdown(url, diagnostics=diagnostics)
            if fallback is not None:
                word_count = len(fallback.split())
                record_content_resolution(
                    stage="universal_html",
                    url=url,
                    success=True,
                    duration_seconds=time.time() - start_time,
                    word_count=word_count,
                    extraction_method="browser",
                )
                return fallback
            return f"_Failed to retrieve GitHub Issue content._\n\nSource: {url}\n"

    try:
        parse_github_discussion_url(url)
    except GitHubDiscussionError:
        pass
    else:
        if diagnostics:
            diagnostics.emit("resolver.route", "Matched GitHub Discussion URL", {"handler": "github_discussion"})
        handler_start = time.time()
        try:
            result = await fetch_github_discussion_thread_markdown(url)
            word_count = len(result.split()) if result else 0
            record_content_resolution(
                stage="github_discussion",
                url=url,
                success=True,
                duration_seconds=time.time() - handler_start,
                word_count=word_count,
                extraction_method="graphql",
            )
            return result
        except Exception:
            record_content_resolution(
                stage="github_discussion",
                url=url,
                success=False,
                duration_seconds=time.time() - handler_start,
            )
            if diagnostics:
                diagnostics.emit(
                    "resolver.fallback",
                    "GitHub Discussion handler failed; falling back to HTML",
                    {"handler": "github_discussion"},
                )
            fallback = await load_url_as_markdown(url, diagnostics=diagnostics)
            if fallback is not None:
                word_count = len(fallback.split())
                record_content_resolution(
                    stage="universal_html",
                    url=url,
                    success=True,
                    duration_seconds=time.time() - start_time,
                    word_count=word_count,
                    extraction_method="browser",
                )
                return fallback
            return f"_Failed to retrieve GitHub Discussion content._\n\nSource: {url}\n"

    try:
        parse_wikipedia_url(url)
    except WikipediaError:
        pass
    else:
        if diagnostics:
            diagnostics.emit("resolver.route", "Matched Wikipedia URL", {"handler": "wikipedia"})
        handler_start = time.time()
        try:
            result = await fetch_wikipedia_article_markdown(url)
            word_count = len(result.split()) if result else 0
            record_content_resolution(
                stage="wikipedia",
                url=url,
                success=True,
                duration_seconds=time.time() - handler_start,
                word_count=word_count,
                extraction_method="api",
            )
            return result
        except Exception:
            record_content_resolution(
                stage="wikipedia",
                url=url,
                success=False,
                duration_seconds=time.time() - handler_start,
            )
            if diagnostics:
                diagnostics.emit(
                    "resolver.fallback",
                    "Wikipedia handler failed; falling back to HTML",
                    {"handler": "wikipedia"},
                )
            fallback = await load_url_as_markdown(url, diagnostics=diagnostics)
            if fallback is not None:
                word_count = len(fallback.split())
                record_content_resolution(
                    stage="universal_html",
                    url=url,
                    success=True,
                    duration_seconds=time.time() - start_time,
                    word_count=word_count,
                    extraction_method="browser",
                )
                return fallback
            return f"_Failed to retrieve Wikipedia content._\n\nSource: {url}\n"

    try:
        parse_arxiv_url(url)
    except ArxivError:
        pass
    else:
        if diagnostics:
            diagnostics.emit("resolver.route", "Matched arXiv URL", {"handler": "arxiv"})
        handler_start = time.time()
        try:
            result = await fetch_arxiv_paper_markdown(url)
            word_count = len(result.split()) if result else 0
            record_content_resolution(
                stage="arxiv",
                url=url,
                success=True,
                duration_seconds=time.time() - handler_start,
                word_count=word_count,
                extraction_method="pdf",
            )
            return result
        except Exception as e:
            record_content_resolution(
                stage="arxiv",
                url=url,
                success=False,
                duration_seconds=time.time() - handler_start,
            )
            if diagnostics:
                diagnostics.emit(
                    "resolver.error",
                    "arXiv handler failed",
                    {"handler": "arxiv", "error": type(e).__name__},
                )
            # arXiv is PDF-based and the universal HTML loader intentionally skips PDFs, so
            # we return a short Markdown error rather than falling back.
            return f"_Failed to retrieve arXiv content: {type(e).__name__}_\n\nSource: {url}\n"

    # Stage 6: HTTP extraction (trafilatura primary, no browser needed)
    if diagnostics:
        diagnostics.emit("resolver.route", "Trying HTTP extraction (trafilatura)", {"handler": "http_extract"})
    handler_start = time.time()
    try:
        result = await http_extract(url, timeout=15.0)
        if result.text and result.word_count >= 50:
            LOGGER.info(f"HTTP extraction succeeded for {url}: {result.word_count} words via {result.method}")
            record_content_resolution(
                stage="http_extract",
                url=url,
                success=True,
                duration_seconds=time.time() - handler_start,
                word_count=result.word_count,
                extraction_method=result.method or "trafilatura",
            )
            if diagnostics:
                diagnostics.emit(
                    "resolver.http_success",
                    "HTTP extraction succeeded",
                    {"handler": "http_extract", "method": result.method, "word_count": result.word_count},
                )
            return result.text
        if diagnostics:
            diagnostics.emit(
                "resolver.http_skip",
                "HTTP extraction result too short, falling back",
                {"handler": "http_extract", "word_count": result.word_count or 0},
            )
    except Exception as e:
        LOGGER.warning(f"HTTP extraction failed for {url}: {e}")
        record_content_resolution(
            stage="http_extract",
            url=url,
            success=False,
            duration_seconds=time.time() - handler_start,
        )
        if diagnostics:
            diagnostics.emit(
                "resolver.http_error",
                "HTTP extraction failed",
                {"handler": "http_extract", "error": type(e).__name__},
            )

    # Stage 7: Universal HTML loader (nodriver/Chromium for JS-heavy sites)
    if diagnostics:
        diagnostics.emit("resolver.route", "Falling back to universal HTML (browser)", {"handler": "universal_html"})
    handler_start = time.time()
    result = await load_url_as_markdown(url, diagnostics=diagnostics)
    if result:
        word_count = len(result.split())
        record_content_resolution(
            stage="universal_html",
            url=url,
            success=True,
            duration_seconds=time.time() - handler_start,
            word_count=word_count,
            extraction_method="browser",
        )
    else:
        record_content_resolution(
            stage="universal_html",
            url=url,
            success=False,
            duration_seconds=time.time() - handler_start,
        )
    return result