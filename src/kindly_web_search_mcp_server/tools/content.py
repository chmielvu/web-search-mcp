from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..cache import get_page_cache
from ..content.batch_orchestrator import BatchParams, run_batch_fetch
from ..content.fetch_pipeline import fetch_content_artifact
from ..content.link_discovery import discover_links as discover_page_links
from ..content.options import build_fetch_options
from ..content.summary import create_batch_summaries, create_summary
from ..content.windowing import slice_content
from ..models import (
    BatchGetContentResponse,
    DiscoverLinksResponse,
    GetContentResponse,
    GetContentResultType,
)
from ..utils.url_canonicalize import canonicalize_url
from ..utils.observability import emit_tool_observability_event
from ._helpers import _get_int_env, _record_tool_success, _resolve_tool_total_timeout_seconds

LOGGER = logging.getLogger(__name__)


async def get_content(
    url: str,
    char_offset: int = 0,
    char_length: int = 20_000,
    summary_mode: str = "none",
    focus_query: str | None = None,
    include_metadata: bool = True,
    include_links: bool = False,
    max_links: int = 25,
    strip_selectors: str | None = None,
    ctx: Context = CurrentContext(),
) -> GetContentResultType:
    """Fetch a single URL as markdown with bounded windowing and 7-stage content resolution.

    Use when you already have a URL. Check window.has_more for pagination continuation.

    Args:
        url: The URL to fetch content from.
        char_offset: Starting character position for windowed content (0-based).
        char_length: Maximum characters to return (default 20k, max 50k).
        summary_mode: "none" (default), "brief" (1-2 sentence), or "detailed"
            (paragraph-level) Gemini URL-context summary.
        focus_query: Topic, term, or comparison to bias the summary toward.
        include_metadata: Include page metadata (title, author, date) in response.
        include_links: Include outbound links discovered on the page.
        max_links: Maximum links to return when include_links is True.
        strip_selectors: CSS selectors to remove from content before extraction
            (e.g., "nav, footer, .sidebar").
    """

    await ctx.report_progress(progress=5, total=100, message="Checking page cache...")
    await ctx.info(f"Fetching: {url[:80]}...")
    emit_tool_observability_event(
        LOGGER,
        "get_content",
        "request",
        url=url,
        char_offset=char_offset,
        char_length=char_length,
        summary_mode=summary_mode,
        focus_query=focus_query,
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )

    max_length = _get_int_env("GET_CONTENT_MAX_CHARS", 50_000)
    safe_length = max(1, min(char_length, max_length))
    safe_offset = max(0, char_offset)
    from ..content.summary_models import VALID_SUMMARY_MODES

    safe_summary_mode = summary_mode if summary_mode in VALID_SUMMARY_MODES else "none"
    fetch_options = build_fetch_options(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )

    artifact: dict[str, Any] | None = None
    normalized_url = canonicalize_url(url)
    try:
        cached = await get_page_cache().alookup(normalized_url)
        if cached:
            cached_metadata = cached.get("metadata")
            cached_page_metadata = (
                cached_metadata.get("metadata")
                if isinstance(cached_metadata, dict) and "metadata" in cached_metadata
                else cached_metadata
            )
            cached_links = (
                cached_metadata.get("links") if isinstance(cached_metadata, dict) else None
            )
            artifact = {
                "input_url": url,
                "normalized_url": normalized_url,
                "fetched_url": None,
                "status": "success",
                "source_type": "cache",
                "fetch_backend": "cache",
                "origin_backend": cached.get("extraction_method") or "cache",
                "cached": True,
                "content_type": "text/markdown",
                "markdown": cached["page_content"],
                "metadata": cached_page_metadata,
                "links": cached_links,
                "word_count": cached.get("word_count", 0) or len(cached["page_content"].split()),
                "error": None,
            }
    except Exception as exc:
        LOGGER.warning("Page cache lookup failed: %s", exc)

    if artifact is None:
        await ctx.report_progress(progress=30, total=100, message="Resolving content...")
        fetched = None
        try:
            fetch_coro = fetch_content_artifact(url, fetch_options=fetch_options)
            try:
                fetched = await asyncio.wait_for(
                    fetch_coro,
                    timeout=_resolve_tool_total_timeout_seconds(),
                )
            except TypeError as exc:
                if "fetch_options" not in str(exc):
                    raise
                fetched = await asyncio.wait_for(
                    fetch_content_artifact(url),
                    timeout=_resolve_tool_total_timeout_seconds(),
                )
        except asyncio.TimeoutError:
            artifact = {
                "input_url": url,
                "normalized_url": normalized_url,
                "fetched_url": None,
                "status": "error",
                "source_type": "unknown",
                "fetch_backend": "timeout",
                "content_type": None,
                "markdown": "",
                "metadata": None,
                "links": None,
                "word_count": 0,
                "error": {
                    "code": "timeout",
                    "message": "Content fetch exceeded the configured tool time budget.",
                    "retryable": True,
                },
            }
        except Exception as exc:
            artifact = {
                "input_url": url,
                "normalized_url": normalized_url,
                "fetched_url": None,
                "status": "error",
                "source_type": "unknown",
                "fetch_backend": "fetch_pipeline",
                "content_type": None,
                "markdown": "",
                "metadata": None,
                "links": None,
                "word_count": 0,
                "error": {
                    "code": type(exc).__name__,
                    "message": str(exc),
                    "retryable": True,
                },
            }
        if fetched is not None:
            artifact = {
                "input_url": fetched.input_url,
                "normalized_url": fetched.normalized_url,
                "fetched_url": fetched.fetched_url,
                "status": fetched.status,
                "source_type": fetched.source_type,
                "fetch_backend": fetched.fetch_backend,
                "content_type": fetched.content_type,
                "markdown": fetched.markdown,
                "metadata": fetched.metadata,
                "links": fetched.links if include_links else None,
                "word_count": fetched.word_count or len(fetched.markdown.split()),
                "error": None
                if fetched.error is None
                else {
                    "code": fetched.error.code,
                    "message": fetched.error.message,
                    "retryable": fetched.error.retryable,
                },
            }
        if fetched is not None and fetched.status == "success" and fetched.markdown:
            try:
                await get_page_cache().astore(
                    canonical_url=fetched.normalized_url,
                    page_content=fetched.markdown,
                    extraction_method=fetched.fetch_backend,
                    metadata={
                        "metadata": fetched.metadata,
                        "links": fetched.links,
                    },
                )
            except Exception as exc:
                LOGGER.warning("Page cache store failed: %s", exc)

    assert artifact is not None
    windowed = slice_content(
        artifact["markdown"],
        offset=safe_offset,
        length=safe_length,
    )
    summary = await create_summary(
        windowed.content,
        mode=safe_summary_mode,  # type: ignore[arg-type]
        focus_query=focus_query,
        source_urls=[
            artifact["fetched_url"] or artifact["normalized_url"],
        ]
        if artifact.get("fetched_url") or artifact.get("normalized_url")
        else None,
    )

    response = GetContentResponse(
        input_url=url,
        normalized_url=artifact["normalized_url"],
        fetched_url=artifact["fetched_url"],
        status=artifact["status"],
        source_type=artifact["source_type"],
        fetch_backend=artifact["fetch_backend"],
        page_content=windowed.content,
        window=windowed.window.__dict__,
        metadata=artifact.get("metadata") if include_metadata else None,
        links=artifact.get("links") if include_links else None,
        continuation_notice=windowed.window.continuation_notice,
        content_type=artifact["content_type"],
        error=artifact["error"],
        summary=summary,
        content_quality=artifact["status"],
        content_word_count=artifact.get("word_count", 0) or len(artifact["markdown"].split()),
    ).model_dump(exclude_none=True)
    fetched_url_val = (
        response.pop("fetched_url", None) or artifact["fetched_url"] or artifact["normalized_url"]
    )
    response.pop("input_url", None)
    response.pop("normalized_url", None)
    response["url"] = fetched_url_val
    response["cached"] = artifact.get("cached", False)
    response["origin_backend"] = artifact.get("origin_backend") or artifact["fetch_backend"]

    await ctx.report_progress(progress=100, total=100, message="Done")
    await ctx.info(
        f"Fetched status={response['status']} chars={len(response['page_content'])} has_more={response['window']['has_more']}"
    )
    emit_tool_observability_event(
        LOGGER,
        "get_content",
        "response",
        input_url=url,
        normalized_url=artifact["normalized_url"],
        fetched_url=fetched_url_val,
        status=response["status"],
        source_type=response["source_type"],
        fetch_backend=response["fetch_backend"],
        content_length=len(response["page_content"]),
        page_char_count=len(response["page_content"]),
        word_count=len(response["page_content"].split()),
        window_offset=response["window"].get("offset"),
        window_length=response["window"].get("length"),
        window_returned_chars=response["window"].get("returned_chars"),
        window_total_chars=response["window"].get("total_chars"),
        window_has_more=response["window"].get("has_more"),
        window_next_offset=response["window"].get("next_offset"),
        page_content=response["page_content"],
        window=response["window"],
        metadata=response.get("metadata"),
        links=response.get("links"),
        continuation_notice=response.get("continuation_notice"),
        content_type=response.get("content_type"),
        error=response.get("error"),
        summary=response.get("summary"),
    )
    _record_tool_success("get_content", output_content=response["page_content"])
    return response  # type: ignore[return-value]


async def batch_get_content(
    urls: list[str] | None = None,
    max_concurrency: int = 4,
    per_item_char_length: int = 8_000,
    total_char_budget: int = 120_000,
    cursor: str | None = None,
    summary_mode: str = "none",
    focus_query: str | None = None,
    include_metadata: bool = True,
    include_links: bool = False,
    max_links: int = 25,
    strip_selectors: str | None = None,
    ctx: Context = CurrentContext(),
) -> BatchGetContentResponse:
    """Fetch multiple URLs in parallel with a total character budget and continuation cursor.

    Prefer over repeated get_content calls when you have 3+ URLs. Check has_more
    and cursor for continuation.

    Args:
        urls: List of URLs to fetch (max 30).
        max_concurrency: Parallel fetch limit (1-8, default 4).
        per_item_char_length: Maximum characters per URL (default 8k, max 50k).
        total_char_budget: Maximum total characters across all URLs (default 120k,
            max 300k). Further URLs are skipped when budget is exhausted.
        cursor: Continuation cursor from a prior response for pagination.
        summary_mode: "none" (default), "brief", or "detailed" per-item summary.
        focus_query: Topic, term, or comparison to bias per-item summaries toward.
        include_metadata: Include page metadata for each result.
        include_links: Include outbound links for each result.
        max_links: Maximum links per result when include_links is True.
        strip_selectors: CSS selectors to remove from content before extraction.
    """
    max_urls = _get_int_env("BATCH_GET_CONTENT_MAX_URLS", 30)
    _urls = urls or []
    bounded_urls: list[str] = _urls[: max(1, max_urls)]
    safe_concurrency = max(1, min(max_concurrency, 8))
    safe_item_length = max(
        500,
        min(per_item_char_length, _get_int_env("GET_CONTENT_MAX_CHARS", 50_000)),
    )
    safe_total_budget = max(
        2_000,
        min(
            total_char_budget,
            _get_int_env("BATCH_TOTAL_CHAR_BUDGET_MAX", 300_000),
        ),
    )

    emit_tool_observability_event(
        LOGGER,
        "batch_get_content",
        "request",
        urls=bounded_urls,
        url_count=len(bounded_urls),
        max_concurrency=safe_concurrency,
        per_item_char_length=safe_item_length,
        total_char_budget=safe_total_budget,
        has_cursor=bool(cursor),
        summary_mode=summary_mode,
        focus_query=focus_query,
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )

    await ctx.info(
        f"Batch fetching {len(bounded_urls)} URLs (concurrency={safe_concurrency}, budget={safe_total_budget})..."
    )
    await ctx.report_progress(
        progress=10, total=100, message=f"Fetching {len(bounded_urls)} URLs..."
    )
    output = await run_batch_fetch(
        urls=bounded_urls,
        params=BatchParams(
            max_concurrency=safe_concurrency,
            per_item_char_length=safe_item_length,
            total_char_budget=safe_total_budget,
            per_url_timeout_seconds=max(
                10.0, _resolve_tool_total_timeout_seconds() / max(len(bounded_urls), 1)
            ),
        ),
        cursor=cursor,
        fetch_options=build_fetch_options(
            include_metadata=include_metadata,
            include_links=include_links,
            max_links=max_links,
            strip_selectors=strip_selectors,
        ),
    )

    from ..content.summary_models import VALID_SUMMARY_MODES

    safe_summary_mode = summary_mode if summary_mode in VALID_SUMMARY_MODES else "none"
    summaries = await create_batch_summaries(
        output["results"],
        mode=safe_summary_mode,  # type: ignore[arg-type]
        focus_query=focus_query,
        max_concurrency=safe_concurrency,
    )

    response = BatchGetContentResponse(
        results=[  # type: ignore[arg-type]
            {
                "input_url": item["input_url"],
                "normalized_url": item["normalized_url"],
                "fetched_url": item["fetched_url"],
                "status": item["status"],
                "source_type": item["source_type"],
                "fetch_backend": item["fetch_backend"],
                "content_type": item.get("content_type"),
                "page_content": item["page_content"],
                "window": item["window"],
                "metadata": item.get("metadata") if include_metadata else None,
                "links": item.get("links") if include_links else None,
                "continuation_notice": item.get("continuation_notice"),
                "error": item.get("error"),
                "summary": summaries[idx],
                "content_quality": item["status"],
                "content_word_count": item.get("word_count") or len(item["page_content"].split()),
            }
            for idx, item in enumerate(output["results"])
        ],
        total_requested=output["total_requested"],
        total_returned=output["total_returned"],
        total_chars_returned=output["total_chars_returned"],
        has_more=output["has_more"],
        cursor=output["cursor"],
    ).model_dump(exclude_none=True)
    for result in response["results"]:
        result.setdefault("fetched_url", None)

    success_count = sum(1 for r in response["results"] if r["status"] == "success")
    await ctx.report_progress(
        progress=100,
        total=100,
        message=f"Done: {success_count}/{len(response['results'])} fetched",
    )
    await ctx.info(
        f"Fetched {success_count}/{len(response['results'])} in this page; has_more={response['has_more']}"
    )
    analytics_results = [
        {
            **result,
            "page_char_count": len(result["page_content"]),
            "word_count": len(result["page_content"].split()),
        }
        for result in response["results"]
    ]
    emit_tool_observability_event(
        LOGGER,
        "batch_get_content",
        "response",
        url_count=len(bounded_urls),
        success_count=success_count,
        error_count=len(response["results"]) - success_count,
        results=analytics_results,
        has_more=response["has_more"],
        cursor=response.get("cursor"),
        total_requested=response.get("total_requested"),
        total_returned=response.get("total_returned"),
        total_chars_returned=response.get("total_chars_returned"),
        total_page_char_count=sum(item["page_char_count"] for item in analytics_results),
        total_word_count=sum(item["word_count"] for item in analytics_results),
    )
    _record_tool_success(
        "batch_get_content",
        input_url_count=len(bounded_urls),
        output_result_count=len(response["results"]),
    )
    return response  # type: ignore[return-value]


async def discover_links(
    url: str,
    max_links: int = 100,
    include_external: bool = True,
    same_domain_only: bool = False,
    strip_selectors: str | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """Extract outbound links from a page or sitemap.

    Returns URLs only — no page content. Use before get_content to discover
    candidate pages, or after to explore related links.

    Args:
        url: The page or sitemap URL to extract links from.
        max_links: Maximum links to return (default 100).
        include_external: Include links to external domains (default True).
        same_domain_only: Only return links from the same domain as the input URL.
        strip_selectors: CSS selectors to exclude from link discovery
            (e.g., "nav, footer, .sidebar").
    """

    await ctx.report_progress(progress=10, total=100, message="Discovering links...")
    await ctx.info(f"Discovering links from: {url[:80]}...")
    emit_tool_observability_event(
        LOGGER,
        "discover_links",
        "request",
        url=url,
        max_links=max_links,
        include_external=include_external,
        same_domain_only=same_domain_only,
        strip_selectors=strip_selectors,
    )

    output = await discover_page_links(
        url,
        max_links=max_links,
        include_external=include_external,
        same_domain_only=same_domain_only,
        strip_selectors=strip_selectors,
    )

    response = DiscoverLinksResponse(
        input_url=output["input_url"],
        normalized_url=output["normalized_url"],
        fetched_url=output.get("fetched_url"),
        source_type=output["source_type"],
        links=output.get("links", []),
        returned_links=output.get("returned_links", 0),
        has_more=output.get("has_more", False),
        metadata=output.get("metadata"),
        error=output.get("error"),
    ).model_dump(exclude_none=True)

    await ctx.report_progress(progress=100, total=100, message="Done")
    await ctx.info(f"Discovered {response['returned_links']} links from {response['source_type']}")
    emit_tool_observability_event(
        LOGGER,
        "discover_links",
        "response",
        url=url,
        source_type=response["source_type"],
        returned_links=response["returned_links"],
        has_more=response["has_more"],
        links=response.get("links", []),
        metadata=response.get("metadata"),
        error=response.get("error"),
    )
    _record_tool_success(
        "discover_links",
        input_url_count=1,
        output_result_count=len(response.get("links", [])),
    )
    return response
