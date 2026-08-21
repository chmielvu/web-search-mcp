from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..cache import get_page_cache
from ..content.fetch_pipeline import fetch_content_artifact
from ..content.options import build_fetch_options
from ..content.summary import create_batch_summaries, create_summary
from ..content.windowing import slice_content
from ..models import (
    BatchGetContentResponse,
    GetContentResponse,
)
from ..utils.url_canonicalize import canonicalize_url
from ..utils.observability import emit_tool_observability_event
from ._helpers import _get_int_env, _record_tool_success, _resolve_tool_total_timeout_seconds

LOGGER = logging.getLogger(__name__)


async def get_content(
    url: str,
    char_offset: int = 0,
    char_length: int = 20_000,
    ai_summary: bool = False,
    focus_query: str | None = None,
    include_metadata: bool = True,
    include_links: bool = True,
    max_links: int = 100,
    strip_selectors: str | None = None,
    ctx: Context = CurrentContext(),
) -> GetContentResponse:
    """Fetch a single URL as markdown with bounded windowing and 7-stage content resolution.

    When to use this tool:
    - When you have a specific target URL and need to read its full text, code blocks, or metadata.
    - After running a search tool to inspect a specific source in detail.

    Pagination & Continuation:
    - This tool returns a windowed chunk of text to save context space.
    - You MUST inspect the 'window.has_more' field in the response.
    - If 'has_more' is true, you MUST call this tool again, setting 'char_offset' to the value of 'window.next_offset'.

    Args:
        url: The URL to fetch content from.
        char_offset: Starting character position for windowed content (0-based).
        char_length: Maximum characters to return (default 20k, max 50k).
        ai_summary: Whether to include a detailed source-grounded Gemini URL-context
            summary (default false).
        focus_query: Topic, term, or comparison to bias the summary toward.
        include_metadata: Include page metadata (title, author, date) in response.
        include_links: Include outbound links discovered on the page (default True).
        max_links: Maximum links to return when include_links is True (default 100).
        strip_selectors: CSS selectors to remove from content before extraction
            (e.g., "nav, footer, .sidebar").
    """
    started = time.monotonic()
    await ctx.report_progress(progress=5, total=100, message="Checking page cache...")
    await ctx.info(f"Fetching: {url[:80]}...")
    emit_tool_observability_event(
        LOGGER,
        "get_content",
        "request",
        url=url,
        char_offset=char_offset,
        char_length=char_length,
        ai_summary=ai_summary,
        focus_query=focus_query,
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )


    max_length = _get_int_env("GET_CONTENT_MAX_CHARS", 50_000)
    safe_length = max(1, min(char_length, max_length))
    safe_offset = max(0, char_offset)
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
            fetched = await asyncio.wait_for(
                fetch_content_artifact(url, fetch_options=fetch_options),
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
        ai_summary=ai_summary,
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
        duration_ms=(time.monotonic() - started) * 1000.0,
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
    ai_summary: bool = False,
    focus_query: str | None = None,
    include_metadata: bool = True,
    include_links: bool = False,
    max_links: int = 25,
    strip_selectors: str | None = None,
    ctx: Context = CurrentContext(),
) -> BatchGetContentResponse:
    """Fetch multiple URLs in parallel with total character budget and continuation cursor.

    When to use this tool:
    - When you need to fetch content from 3 or more URLs concurrently.
    - To efficiently gather page context across multiple sources within a strict character budget.

    Pagination & Continuation:
    - Inspect the 'has_more' and 'cursor' fields in the response.
    - If 'has_more' is true, re-invoke this tool passing the 'cursor' value to get the next batch.

    Do NOT use for:
    - Single or 2 URLs (use get_content instead, it has lower overhead).

    Args:
        urls: List of URLs to fetch (max 30).
        max_concurrency: Parallel fetch limit (1-8, default 4).
        per_item_char_length: Maximum characters per URL (default 8k, max 50k).
        total_char_budget: Maximum total characters across all URLs (default 120k,
            max 300k). Further URLs are skipped when budget is exhausted.
        cursor: Continuation cursor from a prior response for pagination.
        ai_summary: Whether to include a detailed source-grounded Gemini summary
            for each item (default false).
        focus_query: Topic, term, or comparison to bias per-item summaries toward.
        include_metadata: Include page metadata for each result.
        include_links: Include outbound links for each result.
        max_links: Maximum links per result when include_links is True.
        strip_selectors: CSS selectors to remove from content before extraction.
    """
    # ── Resolve pending URLs from cursor or input ────────────────────
    max_urls = _get_int_env("BATCH_GET_CONTENT_MAX_URLS", 30)
    if cursor:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        pending_urls: list[str] = decoded.get("urls", []) or []
    else:
        _urls = urls or []
        pending_urls = list(dict.fromkeys(_urls))[:max_urls]

    if not pending_urls:
        response = BatchGetContentResponse().model_dump(exclude_none=True)
        _record_tool_success(
            "batch_get_content",
            input_url_count=0,
            output_result_count=0,
        )
        return response  # type: ignore[return-value]
    started = time.monotonic()
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
        urls=pending_urls,
        url_count=len(pending_urls),
        max_concurrency=safe_concurrency,
        per_item_char_length=safe_item_length,
        total_char_budget=safe_total_budget,
        has_cursor=bool(cursor),
        ai_summary=ai_summary,
        focus_query=focus_query,
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )
    response_emitted = False
    try:
        await ctx.info(
            f"Batch fetching {len(pending_urls)} URLs (concurrency={safe_concurrency}, budget={safe_total_budget})..."
        )
        await ctx.report_progress(
            progress=10, total=100, message=f"Fetching {len(pending_urls)} URLs..."
        )

        # ── Core: N parallel get_content calls ───────────────────────────
        remaining_budget = safe_total_budget
        results: list[dict] = []
        processed_urls: set[str] = set()

        async def _fetch_one(url: str) -> tuple[str, dict | None]:
            try:
                raw = await get_content(
                    url=url,
                    char_offset=0,
                    char_length=min(safe_item_length, remaining_budget),
                    ai_summary=False,  # summaries batched below
                    focus_query=focus_query,
                    include_metadata=include_metadata,
                    include_links=include_links,
                    max_links=max_links,
                    strip_selectors=strip_selectors,
                    ctx=ctx,
                )
                raw_dict = raw if isinstance(raw, dict) else raw.model_dump(exclude_none=True)
                return (url, raw_dict)
            except Exception as exc:
                LOGGER.warning("get_content failed for %s: %s", url, exc)
                return (url, {
                    "input_url": url,
                    "normalized_url": canonicalize_url(url),
                    "fetched_url": None,
                    "status": "error",
                    "source_type": "unknown",
                    "fetch_backend": "exception",
                    "page_content": "",
                    "window": {},
                    "metadata": None,
                    "links": None,
                    "continuation_notice": None,
                    "content_type": None,
                    "error": {
                        "code": type(exc).__name__,
                        "message": str(exc)[:500],
                        "retryable": True,
                    },
                    "summary": None,
                    "content_quality": "error",
                    "content_word_count": 0,
                })

        for url in pending_urls:
            if remaining_budget <= 0:
                break
            _, raw = await _fetch_one(url)
            if raw is None:
                continue

            page_content = raw.get("page_content", "")
            chars_used = len(page_content)
            if chars_used > remaining_budget:
                break
            processed_urls.add(url)
            remaining_budget -= chars_used

            results.append({
                "input_url": raw.get("input_url") or url,
                "normalized_url": raw.get("normalized_url") or canonicalize_url(url),
                "fetched_url": raw.get("url") or raw.get("fetched_url"),
                "status": raw.get("status", "error"),
                "source_type": raw.get("source_type", "unknown"),
                "fetch_backend": raw.get("fetch_backend", "unknown"),
                "content_type": raw.get("content_type"),
                "page_content": page_content,
                "window": raw.get("window", {}),
                "metadata": raw.get("metadata") if include_metadata else None,
                "links": raw.get("links") if include_links else None,
                "continuation_notice": raw.get("continuation_notice"),
                "error": raw.get("error"),
                "summary": None,
                "content_quality": raw.get("content_quality") or raw.get("status"),
                "content_word_count": raw.get("content_word_count", 0) or len(page_content.split()),
            })
            done = len(results)
            await ctx.report_progress(
                progress=min(90, 10 + int(80 * done / len(pending_urls))),
                total=100,
                message=f"Fetched {done}/{len(pending_urls)} URLs...",
            )

        unconsumed = [u for u in pending_urls if u not in processed_urls]
        has_more = bool(unconsumed)
        next_cursor: str | None = None
        if has_more:
            next_cursor = base64.urlsafe_b64encode(
                json.dumps({"urls": unconsumed}, separators=(",", ":")).encode()
            ).decode()

        summaries = await create_batch_summaries(
            results,
            ai_summary=ai_summary,
            focus_query=focus_query,
            max_concurrency=safe_concurrency,
        )
        for idx, s in enumerate(summaries):
            if idx < len(results):
                results[idx]["summary"] = s

        total_chars = sum(len(r["page_content"]) for r in results)
        success_count = sum(1 for r in results if r["status"] == "success")

        response = BatchGetContentResponse(
            results=results,  # type: ignore[arg-type]
            total_requested=len(pending_urls),
            total_returned=len(results),
            total_chars_returned=total_chars,
            has_more=has_more,
            cursor=next_cursor,
        ).model_dump(exclude_none=True)
        for result in response["results"]:
            result.setdefault("fetched_url", None)

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
            duration_ms=(time.monotonic() - started) * 1000.0,
            url_count=len(pending_urls),
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
        response_emitted = True
        _record_tool_success(
            "batch_get_content",
            input_url_count=len(pending_urls),
            output_result_count=len(response["results"]),
        )
        return response  # type: ignore[return-value]
    finally:
        if not response_emitted:
            emit_tool_observability_event(
                LOGGER,
                "batch_get_content",
                "response",
                status="error",
                duration_ms=(time.monotonic() - started) * 1000.0,
                url_count=len(pending_urls),
            )
    return response  # type: ignore[return-value]


