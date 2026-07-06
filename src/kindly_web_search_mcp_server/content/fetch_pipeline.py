"""Content fetch pipeline — two-tier architecture.

Tier 1: Specialized resolvers (StackExchange, GitHub, Wikipedia, arXiv).
Tier 2: Crawl4AI remote (primary) → fallback.py (Jina Reader → trafilatura).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Awaitable, Callable

from opentelemetry import trace

from ..errors import classify_error
from ..search.normalize import canonicalize_url
from ..settings import settings
from ..telemetry import (
    record_content_error,
    record_content_resolution,
)
from .arxiv import (
    ArxivError,
    fetch_arxiv_paper_markdown,
    parse_arxiv_url,
)
from .artifact import ContentArtifact, ContentError
from .crawl4ai_client import Crawl4AIClientError, get_crawl4ai_client
from .fallback import fallback_fetch_content
from .html_tools import (
    extract_html_metadata,
)
from .options import FetchOptions
from .stackexchange import (
    fetch_stackexchange_thread_markdown,
    parse_stackexchange_url,
)
from .status_classifier import classify_markdown
from .telegram import (
    TelegramContentError,
    fetch_telegram_markdown,
    parse_telegram_url,
)
from .wikipedia import (
    fetch_wikipedia_article_markdown,
    parse_wikipedia_url,
)
from .github_discussions import (
    fetch_github_discussion_thread_markdown,
    parse_github_discussion_url,
)
from .github_issues import (
    fetch_github_issue_thread_markdown,
    parse_github_issue_url,
)

LOGGER = logging.getLogger(__name__)

_content_tracer = trace.get_tracer("kindly_web_search_mcp_server.content.fetch_pipeline")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _to_content_error(exc: Exception, code: str, provider: str | None = None) -> ContentError:
    """Convert any exception to a ContentError with proper error_type and retryable flag."""
    structured = classify_error(exc, provider=provider)
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
    """Try a specialized resolver. Returns None if the URL doesn't match."""
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


def _merge_crawl4ai_links(
    links_data: dict[str, Any],
    *,
    base_url: str,
) -> list[dict[str, Any]]:
    """Convert Crawl4AI links format to our standard link list.

    Crawl4AI returns: {"internal": [{"href": ..., "text": ...}], "external": [...]}
    We return: [{"url": ..., "text": ..., "domain": ..., "internal": bool}]
    """
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    from urllib.parse import urlparse

    for internal in links_data.get("internal") or []:
        href = (internal.get("href") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        parsed = urlparse(href)
        result.append(
            {
                "url": href,
                "text": internal.get("text") or href,
                "domain": parsed.netloc.lower(),
                "internal": True,
            }
        )

    for external in links_data.get("external") or []:
        href = (external.get("href") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        parsed = urlparse(href)
        result.append(
            {
                "url": href,
                "text": external.get("text") or href,
                "domain": parsed.netloc.lower(),
                "internal": False,
            }
        )

    return result


# ------------------------------------------------------------------
# Crawl4AI remote extraction
# ------------------------------------------------------------------


async def _fetch_via_crawl4ai(
    url: str,
    options: FetchOptions,
) -> ContentArtifact:
    """Fetch content via Crawl4AI remote server.

    Uses /crawl endpoint which returns markdown + html + links in one call.
    """
    client = get_crawl4ai_client()
    if client is None:
        raise Crawl4AIClientError("Crawl4AI client not configured", retryable=False)

    results = await client.crawl(url)
    result = results[0]

    if not result.get("success"):
        error_msg = result.get("error_message") or "Crawl4AI crawl failed"
        raise Crawl4AIClientError(error_msg)

    # Extract markdown — prefer fit_markdown (PruningContentFilter)
    md_data = result.get("markdown", {})
    if isinstance(md_data, dict):
        markdown = md_data.get("fit_markdown") or md_data.get("raw_markdown") or ""
    elif isinstance(md_data, str):
        markdown = md_data
    else:
        markdown = ""

    if not markdown.strip():
        raise Crawl4AIClientError("Crawl4AI returned empty markdown")

    # Extract metadata from returned HTML
    html = result.get("cleaned_html") or result.get("html") or ""
    metadata = None
    if options.include_metadata and html:
        metadata = extract_html_metadata(html, page_url=url, fetched_url=result.get("url", url))

    # Extract links from Crawl4AI response
    links = None
    if options.include_links:
        links_data = result.get("links", {})
        if isinstance(links_data, dict):
            links = _merge_crawl4ai_links(links_data, base_url=url)

    cls = classify_markdown(markdown)
    canonical = canonicalize_url(url)

    record_content_resolution(
        stage="crawl4ai_remote",
        url=url,
        success=cls.status == "success",
        size_bytes=len(markdown.encode("utf-8")),
        word_count=len(markdown.split()),
        extraction_method="crawl4ai_remote",
    )

    return ContentArtifact(
        input_url=url,
        normalized_url=canonical,
        fetched_url=result.get("url", url),
        status=cls.status,
        source_type="html",
        fetch_backend="crawl4ai_remote",
        content_type="text/markdown",
        markdown=markdown,
        metadata=metadata,
        links=links,
        word_count=len(markdown.split()),
        quality_score=1.0 if cls.status == "success" else 0.6,
        error=None
        if cls.status == "success"
        else ContentError(
            code=cls.reason or "crawl4ai_low_quality",
            message=cls.reason or "crawl4ai_low_quality",
        ),
    )


# ------------------------------------------------------------------
# Main pipeline
# ------------------------------------------------------------------


async def fetch_content_artifact(
    url: str,
    *,
    fetch_options: FetchOptions | None = None,
) -> ContentArtifact:
    """Fetch content for a URL using the two-tier pipeline.

    Tier 1 — Specialized resolvers (domain-specific, high quality):
      1. StackExchange API (full thread: Q + A + comments)
      2. GitHub Issues API (GraphQL)
      3. GitHub Discussions API (GraphQL)
      4. Wikipedia API (MediaWiki Action API)
      5. arXiv (Atom API + PDF → Markdown)

    Tier 2 — Generic extraction (any URL):
      6. Crawl4AI remote: /crawl → fit_markdown + html + links
         Falls back to Stage 7 if VPS unreachable or fails.
      7. fallback.py: Jina Reader (free) → trafilatura (offline)
    """
    with _content_tracer.start_as_current_span("content.fetch_pipeline") as span:
        span.set_attribute("content.url", url)

        canonical = canonicalize_url(url)
        options = fetch_options or FetchOptions()
        options.validate()

        # ----------------------------------------------------------
        # Tier 1: Specialized resolvers
        # ----------------------------------------------------------

        specialized = await _maybe_specialized(
            url,
            parser=parse_stackexchange_url,  # type: ignore[arg-type]
            fetcher=fetch_stackexchange_thread_markdown,
            source_type="stackexchange",
        )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_github_issue_url,  # type: ignore[arg-type]
        fetcher=fetch_github_issue_thread_markdown,
        source_type="github_issue",
    )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_github_discussion_url,  # type: ignore[arg-type]
        fetcher=fetch_github_discussion_thread_markdown,
        source_type="github_discussion",
    )
    if specialized is not None:
        return specialized

    specialized = await _maybe_specialized(
        url,
        parser=parse_wikipedia_url,  # type: ignore[arg-type]
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
            record_content_error(stage="arxiv", url=url, error_type="arxiv_fetch_failed")
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
                error=_to_content_error(exc, code="arxiv_fetch_failed", provider="arxiv"),
            )

    # Telegram (t.me URLs)
    try:
        parse_telegram_url(url)
    except TelegramContentError:
        pass
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
                normalized_url=canonical,
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
                stage="telegram",
                url=url,
                success=False,
                extraction_method="telethon_mtproto",
            )
            return ContentArtifact(
                input_url=url,
                normalized_url=canonical,
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

    # ----------------------------------------------------------
    # Tier 2: Generic extraction
    # ----------------------------------------------------------

    # Stage 6: Crawl4AI remote (primary)
    client = get_crawl4ai_client()
    if client is not None:
        try:
            return await _fetch_via_crawl4ai(url, options)
        except Crawl4AIClientError as exc:
            LOGGER.warning("Crawl4AI remote failed for %s: %s", url, exc)
            record_content_error(stage="crawl4ai_remote", url=url, error_type="crawl4ai_failed")
            # fall through to fallback

    # Stage 7: Fallback — Jina Reader → trafilatura
    artifact = await fallback_fetch_content(url, options=options)

    # Entity extraction hook: after clean markdown, before return to caller.
    # Only when enabled; uses the shared LLM-backed extractor.
    if settings.entity_extraction_enabled and artifact.markdown:
        try:
            from ..search.entity_extractor import extract_entities
            from ..utils.observability import emit_observability_event

            ents = await extract_entities(artifact.markdown)
            if ents:
                artifact = replace(artifact, entities=ents)
            emit_observability_event(
                LOGGER,
                "entity.content_extracted",
                url=url,
                count=len(ents or []),
                backend=artifact.fetch_backend,
            )
        except Exception as exc:
            emit_observability_event(
                LOGGER,
                "entity.extraction.error",
                url=url,
                error=str(exc)[:300],
                failure_mode="content_extract_failed",
                component="fetch_pipeline",
            )
            # do not fail the fetch

    return artifact
