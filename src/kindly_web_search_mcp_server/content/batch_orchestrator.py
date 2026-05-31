from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass

from .artifact import ContentArtifact, ContentError
from .fetch_pipeline import fetch_content_artifact
from .options import FetchOptions
from .windowing import slice_content


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
    urls: list[str],
    params: BatchParams,
    cursor: str | None,
    fetch_options: FetchOptions | None = None,
) -> dict:
    normalized_urls = _normalize_urls(urls)
    if not normalized_urls:
        return {
            "results": [],
            "total_requested": 0,
            "total_returned": 0,
            "total_chars_returned": 0,
            "has_more": False,
            "cursor": None,
        }

    offsets: dict[str, int] = {u: 0 for u in normalized_urls}
    start_index = 0
    if cursor:
        decoded = _decode_cursor(cursor)
        offsets.update(decoded.get("offsets", {}))
        start_index = int(decoded.get("index", 0))

    sem = asyncio.Semaphore(max(1, min(params.max_concurrency, 8)))

    async def _run(url: str) -> ContentArtifact:
        async with sem:
            try:
                fetch_coro = fetch_content_artifact(url, fetch_options=fetch_options)
                try:
                    return await asyncio.wait_for(
                        fetch_coro,
                        timeout=max(0.001, params.per_url_timeout_seconds),
                    )
                except TypeError as exc:
                    if "fetch_options" not in str(exc):
                        raise
                    return await asyncio.wait_for(
                        fetch_content_artifact(url),
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

    remaining_budget = max(1, params.total_char_budget)
    results: list[dict] = []
    next_index = start_index
    while next_index < len(normalized_urls) and remaining_budget > 0:
        window_urls = normalized_urls[next_index : next_index + params.max_concurrency]
        artifacts = await asyncio.gather(*[_run(url) for url in window_urls])
        stop_for_continuation = False
        for artifact in artifacts:
            if remaining_budget <= 0:
                stop_for_continuation = True
                break

            current_index = normalized_urls.index(artifact.input_url)
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

            if sliced.window.has_more:
                next_index = current_index
                stop_for_continuation = True
                break

            next_index = current_index + 1
        if stop_for_continuation:
            break

    total_chars = sum(len(item["page_content"]) for item in results)
    has_more = next_index < len(normalized_urls)
    next_cursor = None
    if has_more:
        next_cursor = _encode_cursor({"index": next_index, "offsets": offsets})

    return {
        "results": results,
        "total_requested": len(normalized_urls),
        "total_returned": len(results),
        "total_chars_returned": total_chars,
        "has_more": has_more,
        "cursor": next_cursor,
    }
