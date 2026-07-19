from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass

from ..cache import get_page_cache
from ..utils.url_canonicalize import canonicalize_url
from .artifact import ContentArtifact, ContentError
from .firecrawl_stage import run_firecrawl_batch
from .options import FetchOptions
from .windowing import slice_content

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BatchParams:
    max_concurrency: int
    per_item_char_length: int
    total_char_budget: int
    per_url_timeout_seconds: float = 120.0


def _encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> dict:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    return json.loads(raw.decode("utf-8"))


def _normalize_urls(urls: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        candidate = raw.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


async def run_batch_fetch(
    *,
    urls: list[str] | None,
    params: BatchParams,
    cursor: str | None,
    fetch_options: FetchOptions | None = None,
) -> dict:
    if cursor and (not urls or len(urls) == 0):
        decoded = _decode_cursor(cursor)
        urls = decoded.get("urls", []) or []

    if not urls:
        return {
            "results": [],
            "total_requested": 0,
            "total_returned": 0,
            "total_chars_returned": 0,
            "has_more": False,
            "cursor": None,
        }

    normalized_urls = _normalize_urls(urls)

    offsets: dict[str, int] = {u: 0 for u in normalized_urls}
    start_index = 0
    if cursor:
        decoded = _decode_cursor(cursor)
        offsets.update(decoded.get("offsets", {}))
        start_index = int(decoded.get("index", 0))

    sem = asyncio.Semaphore(max(1, min(params.max_concurrency, 8)))

    async def _run_crawl4ai(url: str) -> ContentArtifact:
        """Crawl4AI-only fallback for one URL. Used only when Firecrawl is unavailable."""
        try:
            cached = await get_page_cache().alookup(canonicalize_url(url))
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
                return ContentArtifact(
                    input_url=url,
                    normalized_url=canonicalize_url(url),
                    fetched_url=None,
                    status="success",
                    source_type="cache",
                    fetch_backend="cache",
                    content_type="text/markdown",
                    markdown=cached["page_content"],
                    metadata=cached_page_metadata,
                    links=cached_links,
                    word_count=cached.get("word_count", 0),
                    quality_score=1.0,
                    error=None,
                )
        except Exception as exc:
            LOGGER.warning("Page cache lookup failed in batch for %s: %s", url, exc)

        async with sem:
            from .stages import _fetch_via_crawl4ai

            try:
                return await asyncio.wait_for(
                    _fetch_via_crawl4ai(url, fetch_options or FetchOptions()),
                    timeout=max(0.001, params.per_url_timeout_seconds),
                )
            except asyncio.TimeoutError:
                return ContentArtifact(
                    input_url=url,
                    normalized_url=url,
                    fetched_url=None,
                    status="error",
                    source_type="unknown",
                    fetch_backend="timeout",
                    content_type=None,
                    markdown="",
                    error=ContentError(
                        code="timeout",
                        message="Content fetch exceeded the configured per-URL time budget.",
                        retryable=True,
                    ),
                )
            except Exception as exc:
                return ContentArtifact(
                    input_url=url,
                    normalized_url=canonicalize_url(url),
                    fetched_url=None,
                    status="error",
                    source_type="unknown",
                    fetch_backend="exception",
                    content_type=None,
                    markdown="",
                    error=ContentError(
                        code=type(exc).__name__,
                        message=str(exc)[:500],
                        retryable=True,
                    ),
                )

    remaining_budget = max(1, params.total_char_budget)
    results: list[dict] = []
    # URLs before the cursor index were fully consumed in a prior call.
    processed_urls: set[str] = set(normalized_urls[:start_index])

    def _is_pending(url: str) -> bool:
        return url not in processed_urls or offsets.get(url, 0) > 0

    def _append_result(artifact: ContentArtifact) -> None:
        nonlocal remaining_budget
        if remaining_budget <= 0:
            return

        processed_urls.add(artifact.input_url)
        length = min(params.per_item_char_length, remaining_budget)
        offset = int(offsets.get(artifact.input_url, 0))
        sliced = slice_content(artifact.markdown, offset=offset, length=length)
        offsets[artifact.input_url] = sliced.window.next_offset or 0
        remaining_budget -= sliced.window.returned_chars

        results.append(
            {
                "input_url": artifact.input_url,
                "normalized_url": artifact.normalized_url,
                "fetched_url": artifact.fetched_url,
                "status": artifact.status,
                "source_type": artifact.source_type,
                "fetch_backend": artifact.fetch_backend,
                "content_type": artifact.content_type,
                "page_content": sliced.content,
                "window": sliced.window.__dict__,
                "metadata": artifact.metadata,
                "links": artifact.links,
                "word_count": artifact.word_count or len(artifact.markdown.split()),
                "continuation_notice": sliced.window.continuation_notice,
                "error": None
                if artifact.error is None
                else {
                    "code": artifact.error.code,
                    "message": artifact.error.message,
                    "retryable": artifact.error.retryable,
                },
            }
        )

        if not sliced.window.has_more:
            offsets.pop(artifact.input_url, None)

    pending_urls = [u for u in normalized_urls if _is_pending(u)]

    # Firecrawl Cloud batch scrape is the first backend. None means unavailable
    # or failed -> fall back to Crawl4AI for the whole batch.
    firecrawl_artifacts = await run_firecrawl_batch(
        pending_urls, options=fetch_options, batch_params=params
    )

    if firecrawl_artifacts is not None:
        # Firecrawl ran (success). Slice its artifacts into results. Remaining
        # slices of covered URLs come from the same Firecrawl doc on the next
        # cursor call; this call emits one slice per URL.
        for url in pending_urls:
            if remaining_budget <= 0:
                break
            artifact = firecrawl_artifacts.get(url)
            if artifact is not None:
                _append_result(artifact)
    else:
        # Firecrawl unavailable/failed -> Crawl4AI for the whole batch.
        window_urls: list[str] = []
        for url in pending_urls:
            window_urls.append(url)
            if len(window_urls) >= params.max_concurrency:
                break

        artifacts = await asyncio.gather(*[_run_crawl4ai(url) for url in window_urls])
        for artifact in artifacts:
            if remaining_budget <= 0:
                break
            _append_result(artifact)

    # Find the first index that still has work left; that's where the cursor
    # resumes from. Fully consumed URLs before it are skipped on resume.
    next_index = next(
        (i for i, url in enumerate(normalized_urls) if _is_pending(url)),
        len(normalized_urls),
    )
    total_chars = sum(len(item["page_content"]) for item in results)
    has_more = next_index < len(normalized_urls)
    next_cursor = None
    if has_more:
        next_cursor = _encode_cursor(
            {
                "index": next_index,
                "offsets": offsets,
            }
        )

    return {
        "results": results,
        "total_requested": len(normalized_urls),
        "total_returned": len(results),
        "total_chars_returned": total_chars,
        "has_more": has_more,
        "cursor": next_cursor,
    }
