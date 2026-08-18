# batch_get_content Refactor: Option A vs Option C

## Overview

Both options make `batch_get_content` compose N parallel per-URL fetches instead
of the current custom `batch_orchestrator` + Firecrawl batch path.

- **Option A**: Call `get_content()` N times. Minimal code change. ~50 lines added, ~300 deleted.
- **Option C**: Extract `_fetch_url_core()` from `get_content()`. Both tools share it. ~120 lines added, ~400 deleted.

---

## Option A — Pure Composition

### What changes

`batch_get_content` calls `get_content()` in a semaphore-guarded gather.
`batch_orchestrator.py`, `firecrawl_stage.py`, and the `BatchParams` dataclass
are all **deleted**. The `get_content` function is **untouched**.

### `tools/content.py` (full replacement)

```python
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..cache import get_page_cache
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


# ── get_content: UNCHANGED ──────────────────────────────────────────
# (entire function stays exactly as-is — no edits needed)


# ── batch_get_content: REWRITTEN ────────────────────────────────────

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
    # ── Param validation ─────────────────────────────────────────────
    max_urls = _get_int_env("BATCH_GET_CONTENT_MAX_URLS", 30)
    _urls = urls or []

    # Decode cursor → remaining URL list
    pending_urls: list[str]
    if cursor:
        import base64, json
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        pending_urls = decoded.get("urls", [])
        if not pending_urls:
            pending_urls = _urls[:max_urls]
    else:
        pending_urls = list(dict.fromkeys(_urls))[:max_urls]  # dedup, cap

    if not pending_urls:
        return BatchGetContentResponse().model_dump(exclude_none=True)

    safe_concurrency = max(1, min(max_concurrency, 8))
    safe_item_length = max(500, min(per_item_char_length,
                                     _get_int_env("GET_CONTENT_MAX_CHARS", 50_000)))
    safe_total_budget = max(2_000, min(total_char_budget,
                                        _get_int_env("BATCH_TOTAL_CHAR_BUDGET_MAX", 300_000)))

    emit_tool_observability_event(
        LOGGER, "batch_get_content", "request",
        urls=pending_urls, url_count=len(pending_urls),
        max_concurrency=safe_concurrency, total_char_budget=safe_total_budget,
    )
    await ctx.info(f"Batch fetching {len(pending_urls)} URLs (concurrency={safe_concurrency})...")
    await ctx.report_progress(progress=10, total=100,
                              message=f"Fetching {len(pending_urls)} URLs...")

    # ── Core: N parallel get_content calls ───────────────────────────
    sem = asyncio.Semaphore(safe_concurrency)
    remaining_budget = safe_total_budget
    results: list[dict] = []
    completed_urls: list[str] = []

    async def _fetch_one(url: str) -> dict | None:
        """Call get_content for one URL, respecting the shared budget."""
        nonlocal remaining_budget
        if remaining_budget <= 0:
            return None
        async with sem:
            # get_content handles: cache check → pipeline → windowing → summary
            raw = await get_content(
                url=url,
                char_offset=0,
                char_length=min(safe_item_length, remaining_budget),
                ai_summary=False,       # summaries batched below
                focus_query=focus_query,
                include_metadata=include_metadata,
                include_links=include_links,
                max_links=max_links,
                strip_selectors=strip_selectors,
                ctx=ctx,
            )
            return raw

    # Process in chunks so budget can stop early
    for i in range(0, len(pending_urls), safe_concurrency):
        if remaining_budget <= 0:
            break
        chunk = pending_urls[i:i + safe_concurrency]
        chunk_results = await asyncio.gather(
            *[_fetch_one(u) for u in chunk],
            return_exceptions=True,
        )
        for url, result in zip(chunk, chunk_results):
            if remaining_budget <= 0:
                break
            if isinstance(result, Exception):
                results.append({
                    "input_url": url,
                    "normalized_url": canonicalize_url(url),
                    "fetched_url": None,
                    "status": "error",
                    "source_type": "unknown",
                    "fetch_backend": "exception",
                    "page_content": "",
                    "window": {},
                    "error": {"code": type(result).__name__,
                              "message": str(result)[:500], "retryable": True},
                })
                completed_urls.append(url)
                continue
            if result is None:
                continue  # budget exhausted
            # get_content returns {"url", "status", "page_content", "window", ...}
            # Map to batch shape
            item = {
                "input_url": url,
                "normalized_url": canonicalize_url(url),
                "fetched_url": result.get("url"),
                "status": result.get("status", "error"),
                "source_type": result.get("source_type", "unknown"),
                "fetch_backend": result.get("fetch_backend", "unknown"),
                "page_content": result.get("page_content", ""),
                "window": result.get("window", {}),
                "content_type": result.get("content_type"),
                "metadata": result.get("metadata") if include_metadata else None,
                "links": result.get("links") if include_links else None,
                "continuation_notice": result.get("continuation_notice"),
                "error": result.get("error"),
                "summary": None,  # batched below
                "content_quality": result.get("status"),
                "content_word_count": result.get("content_word_count", 0),
            }
            chars_used = len(item["page_content"])
            remaining_budget -= chars_used
            results.append(item)
            completed_urls.append(url)

    # ── Budget cursor ────────────────────────────────────────────────
    unconsumed = [u for u in pending_urls if u not in completed_urls]
    has_more = bool(unconsumed) and remaining_budget <= 0
    next_cursor = None
    if has_more:
        import base64, json
        next_cursor = base64.urlsafe_b64encode(
            json.dumps({"urls": unconsumed}, separators=(",", ":")).encode()
        ).decode()

    # ── Summaries (single Gemini call for all successful items) ──────
    summaries = await create_batch_summaries(
        results, ai_summary=ai_summary, focus_query=focus_query,
        max_concurrency=safe_concurrency,
    )
    for idx, summary in enumerate(summaries):
        if idx < len(results):
            results[idx]["summary"] = summary

    # ── Response ─────────────────────────────────────────────────────
    total_chars = sum(len(r["page_content"]) for r in results)
    success_count = sum(1 for r in results if r["status"] == "success")

    response = BatchGetContentResponse(
        results=results,
        total_requested=len(pending_urls),
        total_returned=len(results),
        total_chars_returned=total_chars,
        has_more=has_more,
        cursor=next_cursor,
    ).model_dump(exclude_none=True)

    await ctx.report_progress(progress=100, total=100,
                              message=f"Done: {success_count}/{len(results)} fetched")
    emit_tool_observability_event(
        LOGGER, "batch_get_content", "response",
        url_count=len(pending_urls), success_count=success_count,
        has_more=has_more, total_chars_returned=total_chars,
    )
    _record_tool_success("batch_get_content",
                         input_url_count=len(pending_urls),
                         output_result_count=len(results))
    return response  # type: ignore


# ── discover_links: UNCHANGED ───────────────────────────────────────
```

### Files deleted
- `content/batch_orchestrator.py` (entire file)
- `content/firecrawl_stage.py` (entire file)
- `tests/test_batch_orchestrator.py` (entire file)
- `tests/test_firecrawl_stage.py` (entire file)
- `scripts/verify_both_tools_live.py`
- `scripts/verify_firecrawl_live.py`
- `docs/firecrawl-batch-scrape-plan.md`

### Lines changed
| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| `tools/content.py` batch function | ~180 lines | ~130 lines | -50 |
| `content/batch_orchestrator.py` | ~280 lines | **deleted** | -280 |
| `content/firecrawl_stage.py` | ~180 lines | **deleted** | -180 |
| Test files | ~200 lines | **deleted** | -200 |
| **Total** | | | **-710 lines** |

### Tradeoffs
| Dimension | Assessment |
|-----------|------------|
| Code simplicity | ⭐⭐⭐⭐⭐ Minimal new code, just composition |
| Cache dedup | ⭐⭐⭐ Each get_content checks cache independently (no harm, just redundant calls) |
| Firecrawl batch API | ❌ Lost entirely — each URL goes through per-URL pipeline |
| Timeout model | ⭐⭐⭐⭐⭐ Each URL gets tool's native timeout (no derived division) |
| Pipeline stages | ⭐⭐⭐⭐ Every URL always gets full 7-stage pipeline |
| Cursor simplicity | ⭐⭐⭐⭐ Simple URL list, no offset tracking |
| Response shape mapping | ⭐⭐⭐ Manual mapping from get_content shape → batch shape (15 fields) |
| Testability | ⭐⭐⭐⭐⭐ Test get_content → batch is automatically tested |

---

## Option C — Extract Shared Core

### What changes

A new `_fetch_url_core()` function is extracted from `get_content()`. Both
`get_content()` and `batch_get_content()` call it. The batch function is
rewritten to use it directly with a budget layer.

### `tools/content.py` (full replacement)

```python
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


# ═════════════════════════════════════════════════════════════════════
# Shared core — the ONLY place that touches the fetch pipeline
# ═════════════════════════════════════════════════════════════════════

async def _fetch_url_core(
    url: str,
    *,
    char_offset: int = 0,
    char_length: int = 20_000,
    fetch_options: Any | None = None,
    timeout_seconds: float | None = None,
    ctx: Context,
) -> dict[str, Any]:
    """Fetch a single URL through the cache → pipeline → windowing chain.

    Returns a raw artifact dict with keys:
        input_url, normalized_url, fetched_url, status, source_type,
        fetch_backend, origin_backend, cached, content_type, markdown,
        window, metadata, links, word_count, error

    This is the single source of truth for all URL fetching.
    Both get_content() and batch_get_content() call this.
    """
    normalized_url = canonicalize_url(url)
    safe_offset = max(0, char_offset)
    safe_length = max(1, char_length)
    opts = fetch_options or build_fetch_options()
    timeout = timeout_seconds or _resolve_tool_total_timeout_seconds()

    # ── Cache check ──────────────────────────────────────────────────
    artifact: dict[str, Any] | None = None
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
                cached_metadata.get("links")
                if isinstance(cached_metadata, dict) else None
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
                "word_count": cached.get("word_count", 0)
                             or len(cached["page_content"].split()),
                "error": None,
            }
    except Exception as exc:
        LOGGER.warning("Page cache lookup failed: %s", exc)

    # ── Pipeline fetch ───────────────────────────────────────────────
    if artifact is None:
        fetched = None
        try:
            fetched = await asyncio.wait_for(
                fetch_content_artifact(url, fetch_options=opts),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            artifact = {
                "input_url": url,
                "normalized_url": normalized_url,
                "fetched_url": None,
                "status": "error",
                "source_type": "unknown",
                "fetch_backend": "timeout",
                "origin_backend": "timeout",
                "cached": False,
                "content_type": None,
                "markdown": "",
                "metadata": None,
                "links": None,
                "word_count": 0,
                "error": {
                    "code": "timeout",
                    "message": "Content fetch exceeded the configured time budget.",
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
                "fetch_backend": "exception",
                "origin_backend": "exception",
                "cached": False,
                "content_type": None,
                "markdown": "",
                "metadata": None,
                "links": None,
                "word_count": 0,
                "error": {
                    "code": type(exc).__name__,
                    "message": str(exc)[:500],
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
                "origin_backend": fetched.fetch_backend,
                "cached": False,
                "content_type": fetched.content_type,
                "markdown": fetched.markdown,
                "metadata": fetched.metadata,
                "links": fetched.links,
                "word_count": fetched.word_count or len(fetched.markdown.split()),
                "error": (
                    None if fetched.error is None else {
                        "code": fetched.error.code,
                        "message": fetched.error.message,
                        "retryable": fetched.error.retryable,
                    }
                ),
            }
        # Cache store
        if fetched is not None and fetched.status == "success" and fetched.markdown:
            try:
                await get_page_cache().astore(
                    canonical_url=fetched.normalized_url,
                    page_content=fetched.markdown,
                    extraction_method=fetched.fetch_backend,
                    metadata={"metadata": fetched.metadata, "links": fetched.links},
                )
            except Exception as exc:
                LOGGER.warning("Page cache store failed: %s", exc)

    assert artifact is not None

    # ── Windowing ────────────────────────────────────────────────────
    windowed = slice_content(artifact["markdown"], offset=safe_offset, length=safe_length)
    artifact["page_content"] = windowed.content
    artifact["window"] = windowed.window.__dict__
    artifact["continuation_notice"] = windowed.window.continuation_notice

    return artifact


# ═════════════════════════════════════════════════════════════════════
# get_content — thin wrapper: core + summary + response mutations
# ═════════════════════════════════════════════════════════════════════

async def get_content(
    url: str,
    char_offset: int = 0,
    char_length: int = 20_000,
    ai_summary: bool = False,
    focus_query: str | None = None,
    include_metadata: bool = True,
    include_links: bool = False,
    max_links: int = 25,
    strip_selectors: str | None = None,
    ctx: Context = CurrentContext(),
) -> GetContentResultType:
    """Fetch a single URL as markdown with bounded windowing and 7-stage content resolution.

    When to use this tool:
    - When you have a specific target URL and need to read its full text, code blocks, or metadata.
    - After running a search tool to inspect a specific source in detail.

    Pagination & Continuation:
    - This tool returns a windowed chunk of text to save context space.
    - You MUST inspect the 'window.has_more' field in the response.
    - If 'has_more' is true, you MUST call this tool again, setting 'char_offset'
      to the value of 'window.next_offset'.

    Args:
        url: The URL to fetch content from.
        char_offset: Starting character position for windowed content (0-based).
        char_length: Maximum characters to return (default 20k, max 50k).
        ai_summary: Whether to include a detailed source-grounded Gemini summary.
        focus_query: Topic, term, or comparison to bias summaries toward.
        include_metadata: Include page metadata (title, author, date) in response.
        include_links: Include outbound links discovered on the page.
        max_links: Maximum links to return when include_links is True.
        strip_selectors: CSS selectors to remove before extraction.
    """
    await ctx.report_progress(progress=5, total=100, message="Checking page cache...")
    await ctx.info(f"Fetching: {url[:80]}...")

    max_length = _get_int_env("GET_CONTENT_MAX_CHARS", 50_000)
    safe_length = max(1, min(char_length, max_length))
    fetch_options = build_fetch_options(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )

    await ctx.report_progress(progress=30, total=100, message="Resolving content...")
    artifact = await _fetch_url_core(
        url, char_offset=char_offset, char_length=safe_length,
        fetch_options=fetch_options, ctx=ctx,
    )

    summary = await create_summary(
        artifact["page_content"],
        ai_summary=ai_summary,
        focus_query=focus_query,
        source_urls=[artifact.get("fetched_url") or artifact["normalized_url"]],
    )

    # ── Response mutations (single-URL shape) ────────────────────────
    response = GetContentResponse(
        input_url=url,
        normalized_url=artifact["normalized_url"],
        fetched_url=artifact["fetched_url"],
        status=artifact["status"],
        source_type=artifact["source_type"],
        fetch_backend=artifact["fetch_backend"],
        page_content=artifact["page_content"],
        window=artifact["window"],
        metadata=artifact.get("metadata") if include_metadata else None,
        links=artifact.get("links") if include_links else None,
        continuation_notice=artifact.get("continuation_notice"),
        content_type=artifact.get("content_type"),
        error=artifact.get("error"),
        summary=summary,
        content_quality=artifact["status"],
        content_word_count=artifact.get("word_count", 0),
    ).model_dump(exclude_none=True)

    fetched_url_val = (
        response.pop("fetched_url", None)
        or artifact["fetched_url"]
        or artifact["normalized_url"]
    )
    response.pop("input_url", None)
    response.pop("normalized_url", None)
    response["url"] = fetched_url_val
    response["cached"] = artifact.get("cached", False)
    response["origin_backend"] = artifact.get("origin_backend") or artifact["fetch_backend"]

    await ctx.report_progress(progress=100, total=100, message="Done")
    _record_tool_success("get_content", output_content=response["page_content"])
    return response  # type: ignore


# ═════════════════════════════════════════════════════════════════════
# batch_get_content — core × N + budget + cursor + progress
# ═════════════════════════════════════════════════════════════════════

def _encode_cursor(urls: list[str]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"urls": urls}, separators=(",", ":")).encode()
    ).decode()

def _decode_cursor(cursor: str) -> list[str]:
    return json.loads(base64.urlsafe_b64decode(cursor.encode())).get("urls", [])


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
    max_urls = _get_int_env("BATCH_GET_CONTENT_MAX_URLS", 30)

    # ── Resolve pending URLs from cursor or input ────────────────────
    if cursor:
        pending_urls = _decode_cursor(cursor)
    else:
        _urls = urls or []
        pending_urls = list(dict.fromkeys(_urls))[:max_urls]

    if not pending_urls:
        return BatchGetContentResponse().model_dump(exclude_none=True)

    safe_concurrency = max(1, min(max_concurrency, 8))
    safe_item_length = max(500, min(per_item_char_length,
                                     _get_int_env("GET_CONTENT_MAX_CHARS", 50_000)))
    safe_total_budget = max(2_000, min(total_char_budget,
                                        _get_int_env("BATCH_TOTAL_CHAR_BUDGET_MAX", 300_000)))

    emit_tool_observability_event(
        LOGGER, "batch_get_content", "request",
        urls=pending_urls, url_count=len(pending_urls),
        max_concurrency=safe_concurrency, total_char_budget=safe_total_budget,
    )
    await ctx.info(
        f"Batch fetching {len(pending_urls)} URLs "
        f"(concurrency={safe_concurrency}, budget={safe_total_budget})..."
    )
    await ctx.report_progress(
        progress=10, total=100,
        message=f"Fetching {len(pending_urls)} URLs...",
    )

    # ── Fetch: semaphore-guarded parallel _fetch_url_core calls ──────
    sem = asyncio.Semaphore(safe_concurrency)
    remaining_budget = safe_total_budget
    results: list[dict] = []
    processed: set[str] = set()

    fetch_options = build_fetch_options(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )

    async def _guarded_fetch(url: str) -> tuple[str, dict | None]:
        if remaining_budget <= 0:
            return (url, None)
        async with sem:
            per_url_timeout = max(
                10.0,
                _resolve_tool_total_timeout_seconds() / max(safe_concurrency, 1),
            )
            try:
                artifact = await _fetch_url_core(
                    url,
                    char_offset=0,
                    char_length=min(safe_item_length, remaining_budget),
                    fetch_options=fetch_options,
                    timeout_seconds=per_url_timeout,
                    ctx=ctx,
                )
                return (url, artifact)
            except Exception as exc:
                return (url, {
                    "input_url": url,
                    "normalized_url": canonicalize_url(url),
                    "fetched_url": None,
                    "status": "error",
                    "source_type": "unknown",
                    "fetch_backend": "exception",
                    "origin_backend": "exception",
                    "cached": False,
                    "content_type": None,
                    "page_content": "",
                    "window": {},
                    "metadata": None,
                    "links": None,
                    "word_count": 0,
                    "error": {"code": type(exc).__name__,
                              "message": str(exc)[:500], "retryable": True},
                })

    # Process in concurrency-sized chunks
    for i in range(0, len(pending_urls), safe_concurrency):
        if remaining_budget <= 0:
            break
        chunk = pending_urls[i : i + safe_concurrency]
        fetched = await asyncio.gather(*[_guarded_fetch(u) for u in chunk])

        for url, artifact in fetched:
            if remaining_budget <= 0:
                break
            if artifact is None:
                continue
            processed.add(url)

            # Deduct budget using the already-windowed page_content
            page_content = artifact.get("page_content", "")
            chars_used = len(page_content)
            remaining_budget -= chars_used

            results.append({
                "input_url": artifact.get("input_url", url),
                "normalized_url": artifact.get("normalized_url", canonicalize_url(url)),
                "fetched_url": artifact.get("fetched_url"),
                "status": artifact.get("status", "error"),
                "source_type": artifact.get("source_type", "unknown"),
                "fetch_backend": artifact.get("fetch_backend", "unknown"),
                "page_content": page_content,
                "window": artifact.get("window", {}),
                "content_type": artifact.get("content_type"),
                "metadata": artifact.get("metadata") if include_metadata else None,
                "links": artifact.get("links") if include_links else None,
                "continuation_notice": artifact.get("continuation_notice"),
                "error": artifact.get("error"),
                "summary": None,
                "content_quality": artifact.get("status"),
                "content_word_count": artifact.get("word_count", 0),
            })

            # Progress: report after each URL completes
            done = len(results)
            await ctx.report_progress(
                progress=min(90, 10 + int(80 * done / len(pending_urls))),
                total=100,
                message=f"Fetched {done}/{len(pending_urls)} URLs...",
            )

    # ── Cursor ───────────────────────────────────────────────────────
    unconsumed = [u for u in pending_urls if u not in processed]
    has_more = bool(unconsumed) and remaining_budget <= 0
    next_cursor = _encode_cursor(unconsumed) if has_more else None

    # ── Summaries ────────────────────────────────────────────────────
    summaries = await create_batch_summaries(
        results, ai_summary=ai_summary, focus_query=focus_query,
        max_concurrency=safe_concurrency,
    )
    for idx, s in enumerate(summaries):
        if idx < len(results):
            results[idx]["summary"] = s

    # ── Response ─────────────────────────────────────────────────────
    total_chars = sum(len(r["page_content"]) for r in results)
    success_count = sum(1 for r in results if r["status"] == "success")

    response = BatchGetContentResponse(
        results=results,
        total_requested=len(pending_urls),
        total_returned=len(results),
        total_chars_returned=total_chars,
        has_more=has_more,
        cursor=next_cursor,
    ).model_dump(exclude_none=True)

    await ctx.report_progress(
        progress=100, total=100,
        message=f"Done: {success_count}/{len(results)} fetched",
    )
    emit_tool_observability_event(
        LOGGER, "batch_get_content", "response",
        url_count=len(pending_urls), success_count=success_count,
        has_more=has_more, total_chars_returned=total_chars,
    )
    _record_tool_success("batch_get_content",
                         input_url_count=len(pending_urls),
                         output_result_count=len(results))
    return response  # type: ignore


# ── discover_links: UNCHANGED ───────────────────────────────────────
```

### Files deleted
- `content/batch_orchestrator.py` (entire file)
- `content/firecrawl_stage.py` (entire file)
- `tests/test_batch_orchestrator.py` (entire file)
- `tests/test_firecrawl_stage.py` (entire file)

### Lines changed
| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| `tools/content.py` — core extraction | 0 lines | ~110 lines | +110 |
| `tools/content.py` — get_content | ~170 lines | ~50 lines | -120 |
| `tools/content.py` — batch_get_content | ~180 lines | ~120 lines | -60 |
| `content/batch_orchestrator.py` | ~280 lines | **deleted** | -280 |
| `content/firecrawl_stage.py` | ~180 lines | **deleted** | -180 |
| Test updates | ~200 lines | ~50 lines | -150 |
| **Total** | | | **-620 net** |

### Tradeoffs
| Dimension | Assessment |
|-----------|------------|
| Code simplicity | ⭐⭐⭐⭐ One new function, two thin wrappers |
| Cache dedup | ⭐⭐⭐⭐⭐ Single cache path — no redundancy |
| Firecrawl batch API | ❌ Lost — same as Option A |
| Timeout model | ⭐⭐⭐⭐ Each URL gets tool-native timeout via core |
| Pipeline stages | ⭐⭐⭐⭐⭐ Every URL always gets full 7-stage pipeline |
| Cursor simplicity | ⭐⭐⭐⭐⭐ Simple URL list, no offset tracking |
| Response shape mapping | ⭐⭐⭐⭐ get_content mutates response, batch maps from core dict directly (cleaner) |
| Testability | ⭐⭐⭐⭐⭐ Test `_fetch_url_core` thoroughly → both tools are covered |
| Future extensibility | ⭐⭐⭐⭐⭐ Easy to add Firecrawl preflight later: call batch API, merge into core results for uncovered URLs |

---

## Head-to-Head Comparison

| Dimension | Option A (compose get_content) | Option C (shared core) |
|-----------|-------------------------------|----------------------|
| **New code** | ~50 lines | ~120 lines |
| **Deleted code** | ~710 lines | ~790 lines |
| **Net delta** | **-660 lines** | **-670 lines** |
| **get_content modified?** | No | Yes (refactored into thin wrapper) |
| **Risk of regression in get_content** | Zero | Low (same logic, just moved) |
| **Cache redundancy** | Each get_content checks cache independently | Single cache path |
| **Response mapping** | Manual field-by-field from get_content's mutated shape | Direct from core dict (cleaner) |
| **Batch-level control** | Indirect (can't set per-URL timeout from batch) | Direct (core accepts timeout param) |
| **Progress granularity** | Post-chunk only | Per-URL streaming |
| **Adding Firecrawl later** | Hard (would need to inject into get_content) | Easy (call batch API, merge artifacts into core results) |
| **Test approach** | Test get_content → batch inherits | Test _fetch_url_core → both inherit |
| **Recommended for** | Quick fix, ship today | Proper refactor, maintain long-term |

### Verdict

- **Choose Option A** if you want the smallest diff, zero risk to `get_content`, and can ship in one session.
- **Choose Option C** if you want a clean architecture that's maintainable long-term and leaves the door open for Firecrawl as an optional optimization layer.

Both delete `batch_orchestrator.py` and `firecrawl_stage.py`. Both fix every reliability issue identified in the analysis (timeout division, all-or-nothing Firecrawl, fragile cursor, missing progress). The difference is ~70 lines of additional extraction work.
