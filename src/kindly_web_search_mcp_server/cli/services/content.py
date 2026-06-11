from __future__ import annotations

import asyncio
import os
from typing import Any

from ...cache import get_page_cache
from ...content.fetch_pipeline import fetch_content_artifact
from ...content.options import build_fetch_options
from ...content.summary import create_summary
from ...content.windowing import slice_content
from ...models import GetContentResponse
from ...search.normalize import canonicalize_url


def _get_int_env(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _get_float_env(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _resolve_tool_total_timeout_seconds() -> float:
    value = _get_float_env("TOOL_TOTAL_TIMEOUT_SECONDS", 120.0)
    max_value = _get_float_env("TOOL_TOTAL_TIMEOUT_MAX_SECONDS", 600.0)
    return max(1.0, min(value, max(1.0, max_value)))


def _cached_artifact(url: str) -> dict[str, Any] | None:
    try:
        cached = get_page_cache().lookup(url)
    except Exception:
        return None
    if not cached:
        return None

    cached_metadata = cached.get("metadata")
    cached_page_metadata = (
        cached_metadata.get("metadata")
        if isinstance(cached_metadata, dict) and "metadata" in cached_metadata
        else cached_metadata
    )
    cached_links = (
        cached_metadata.get("links") if isinstance(cached_metadata, dict) else None
    )
    return {
        "input_url": url,
        "normalized_url": url,
        "fetched_url": None,
        "status": "success",
        "source_type": "cache",
        "fetch_backend": cached.get("extraction_method") or "cache",
        "content_type": "text/markdown",
        "markdown": cached["page_content"],
        "metadata": cached_page_metadata,
        "links": cached_links,
        "error": None,
    }


def _artifact_from_fetch_exception(url: str, exc: Exception) -> dict[str, Any]:
    return {
        "input_url": url,
        "normalized_url": canonicalize_url(url),
        "fetched_url": None,
        "status": "error",
        "source_type": "unknown",
        "fetch_backend": "fetch_pipeline",
        "content_type": None,
        "markdown": "",
        "metadata": None,
        "links": None,
        "error": {
            "code": type(exc).__name__,
            "message": str(exc),
            "retryable": True,
        },
    }


def _artifact_from_timeout(url: str) -> dict[str, Any]:
    return {
        "input_url": url,
        "normalized_url": canonicalize_url(url),
        "fetched_url": None,
        "status": "error",
        "source_type": "unknown",
        "fetch_backend": "timeout",
        "content_type": None,
        "markdown": "",
        "metadata": None,
        "links": None,
        "error": {
            "code": "timeout",
            "message": "Content fetch exceeded the configured tool time budget.",
            "retryable": True,
        },
    }


def _artifact_from_result(fetched: Any, *, include_links: bool) -> dict[str, Any]:
    return {
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
        "error": None
        if fetched.error is None
        else {
            "code": fetched.error.code,
            "message": fetched.error.message,
            "retryable": fetched.error.retryable,
        },
    }


async def fetch_content_payload(
    url: str,
    *,
    char_offset: int = 0,
    char_length: int = 20_000,
    summary_mode: str = "none",
    focus_query: str | None = None,
    include_metadata: bool = True,
    include_links: bool = False,
    max_links: int = 25,
    strip_selectors: str | None = None,
) -> dict[str, Any]:
    max_length = _get_int_env("GET_CONTENT_MAX_CHARS", 50_000)
    safe_length = max(1, min(char_length, max_length))
    safe_offset = max(0, char_offset)
    safe_summary_mode = (
        summary_mode if summary_mode in {"none", "brief", "detailed"} else "none"
    )

    fetch_options = build_fetch_options(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )

    normalized_url = canonicalize_url(url)
    artifact = _cached_artifact(normalized_url)
    if artifact is None:
        try:
            fetched = await asyncio.wait_for(
                fetch_content_artifact(url, fetch_options=fetch_options),
                timeout=_resolve_tool_total_timeout_seconds(),
            )
        except asyncio.TimeoutError:
            artifact = _artifact_from_timeout(url)
        except Exception as exc:
            artifact = _artifact_from_fetch_exception(url, exc)
        else:
            artifact = _artifact_from_result(fetched, include_links=include_links)
            if fetched.status == "success" and fetched.markdown:
                try:
                    get_page_cache().store(
                        canonical_url=fetched.normalized_url,
                        page_content=fetched.markdown,
                        extraction_method=fetched.fetch_backend,
                        metadata={
                            "metadata": fetched.metadata,
                            "links": fetched.links,
                        },
                    )
                except Exception:
                    pass

    windowed = slice_content(
        artifact["markdown"],
        offset=safe_offset,
        length=safe_length,
    )
    summary = await create_summary(
        windowed.content,
        mode=safe_summary_mode,
        focus_query=focus_query,
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
    ).model_dump(exclude_none=True)
    response.setdefault("fetched_url", None)
    return response
