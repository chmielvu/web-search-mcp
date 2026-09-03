from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import Any, Literal

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..cache import get_page_cache
from ..content.artifact import ContentArtifact, artifact_to_dict
from ..errors import raise_tool_error
from ..content.fetch_pipeline import fetch_content_artifact
from ..content.llms_txt import LlmsTxtResult, check_llms_txt
from ..content.options import FetchOptions, build_fetch_options
from ..content.status_classifier import classify_markdown, wall_from_classification
from ..content.summary import create_batch_summaries, create_summary
from ..content.windowing import slice_content
from ..models import FetchResponse, FetchResult, TokenUsage
from ..settings import settings
from ..utils.observability import emit_tool_observability_event
from ..utils.url_canonicalize import canonicalize_url
from ._helpers import _record_tool_success

LOGGER = logging.getLogger(__name__)

_CURSOR_VERSION = 1
_CACHE_SCHEMA_VERSION = 3
_CACHE_ROUTE_VERSION = 4
_TYPED_FORMATS = {
    "json",
    "jsonl",
    "yaml",
    "toml",
    "rss",
    "atom",
    "xml",
    "csv",
    "tsv",
    "rtf",
    "vtt",
    "srt",
    "svg",
}


def _cache_key(normalized_url: str) -> str:
    """Isolate fetch results from caches created under older route rules."""
    return f"web-fetch-route-v{_CACHE_ROUTE_VERSION}:{normalized_url}"


def _error_dict(exc: Exception) -> dict[str, Any]:
    retryable = isinstance(
        exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError)
    )
    return {
        "code": type(exc).__name__,
        "message": str(exc)[:500],
        "retryable": retryable,
    }


def _content_format(source_type: str, content_type: str | None) -> str:
    lowered = (content_type or "").split(";", 1)[0].strip().lower()
    if source_type in _TYPED_FORMATS or source_type == "llms_txt":
        return source_type
    if "json" in lowered:
        return "json"
    if "rss" in lowered:
        return "rss"
    if "atom" in lowered:
        return "atom"
    if "csv" in lowered:
        return "csv"
    if "pdf" in lowered or source_type == "pdf":
        return "pdf"
    return "markdown"


def _apply_status_error_invariant(artifact: dict[str, Any]) -> dict[str, Any]:
    if artifact.get("status") == "success":
        artifact["error"] = None
    return artifact




def _request_fingerprint(
    *,
    fetch_options: FetchOptions,
    focus_query: str | None,
    ai_summary: bool,
) -> str:
    payload = {
        "fetch_options": fetch_options.cache_fingerprint(),
        "focus_query": focus_query or "",
        "ai_summary": ai_summary,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _encode_cursor(urls: list[str], fingerprint: str) -> str:
    payload = {
        "version": _CURSOR_VERSION,
        "mode": "bulk",
        "urls": urls,
        "fingerprint": fingerprint,
    }
    return base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
    except Exception:
        raise_tool_error(ValueError("Invalid fetch cursor"), provider="fetch")
    if not isinstance(decoded, dict) or decoded.get("version") != _CURSOR_VERSION:
        raise_tool_error(ValueError("Unsupported fetch cursor version"), provider="fetch")
    if decoded.get("mode") != "bulk" or not isinstance(decoded.get("urls"), list):
        raise_tool_error(ValueError("Invalid bulk fetch cursor"), provider="fetch")
    urls = [item.strip() for item in decoded["urls"] if isinstance(item, str) and item.strip()]
    if not urls:
        raise_tool_error(ValueError("Fetch cursor contains no pending URLs"), provider="fetch")
    decoded["urls"] = list(dict.fromkeys(urls))
    return decoded



def _artifact_from_cache(input_url: str, normalized_url: str, cached: dict[str, Any]) -> dict[str, Any]:
    stored = cached.get("metadata")
    stored = stored if isinstance(stored, dict) else {}
    envelope = stored.get("__web_fetch__")
    envelope = envelope if isinstance(envelope, dict) else {}
    legacy = (
        not envelope
        or envelope.get("schema_version") != _CACHE_SCHEMA_VERSION
        or envelope.get("route_version") != _CACHE_ROUTE_VERSION
    )

    metadata = envelope.get("metadata") if isinstance(envelope.get("metadata"), dict) else stored.get("metadata")
    links = envelope.get("links") if isinstance(envelope.get("links"), list) else stored.get("links")
    origin_backend = envelope.get("origin_backend") or cached.get("extraction_method") or "cache"
    source_type = envelope.get("source_type") or ("cache_legacy" if legacy else "html")
    fetched_url = envelope.get("fetched_url") or cached.get("url_canonical") or normalized_url
    content_type = envelope.get("content_type") or "text/markdown"
    status = envelope.get("status") or "success"
    diagnostics = list(envelope.get("diagnostics") or [])
    entities = envelope.get("entities")
    entities = entities if isinstance(entities, list) else None
    if legacy:
        diagnostics.append(
            {
                "code": "legacy_cache_entry",
                "cache_schema_version": envelope.get("schema_version", 0),
                "cache_route_version": envelope.get("route_version", 0),
            }
        )

    artifact = {
        "input_url": input_url,
        "normalized_url": str(envelope.get("normalized_url") or normalized_url),
        "fetched_url": str(fetched_url) if fetched_url else None,
        "status": str(status),
        "source_type": str(source_type),
        "fetch_backend": "cache",
        "origin_backend": str(origin_backend),
        "cached": True,
        "content_type": str(content_type) if content_type else None,
        "markdown": str(cached.get("page_content") or ""),
        "metadata": metadata if isinstance(metadata, dict) else None,
        "links": links if isinstance(links, list) else None,
        "error": envelope.get("error") if isinstance(envelope.get("error"), dict) else None,
        "entities": entities,
        "llms_txt": (
            envelope.get("llms_txt") if isinstance(envelope.get("llms_txt"), dict) else None
        ),
        "diagnostics": diagnostics,
    }
    return _apply_status_error_invariant(artifact)

def _cache_metadata(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "__web_fetch__": {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "route_version": _CACHE_ROUTE_VERSION,
            "input_url": artifact["input_url"],
            "normalized_url": artifact["normalized_url"],
            "fetched_url": artifact.get("fetched_url"),
            "status": artifact.get("status"),
            "source_type": artifact.get("source_type"),
            "content_type": artifact.get("content_type"),
            "origin_backend": artifact.get("origin_backend") or artifact.get("fetch_backend"),
            "metadata": artifact.get("metadata"),
            "links": artifact.get("links"),
            "entities": artifact.get("entities"),
            "llms_txt": artifact.get("llms_txt"),
            "diagnostics": artifact.get("diagnostics"),
            "error": artifact.get("error"),
        },
        "metadata": artifact.get("metadata"),
        "links": artifact.get("links"),
        "origin_backend": artifact.get("origin_backend") or artifact.get("fetch_backend"),
        "status_code": 200,
    }


async def _store_cache(artifact: dict[str, Any]) -> None:
    status = artifact.get("status")
    if status not in {"success", "partial", "blocked", "error"} or not artifact.get("markdown"):
        return
    try:
        await get_page_cache().astore(
            canonical_url=_cache_key(artifact["normalized_url"]),
            page_content=artifact["markdown"],
            extraction_method=artifact.get("origin_backend") or artifact.get("fetch_backend") or "unknown",
            metadata=_cache_metadata(artifact),
        )
    except Exception as exc:  # pragma: no cover - cache isolation
        LOGGER.warning("Page cache store failed: %s", exc)


def _artifact_from_llms(input_url: str, probe: LlmsTxtResult) -> dict[str, Any]:
    content = probe.content or ""
    classification = classify_markdown(content)
    status = "success" if classification.status in {"success", "partial"} else classification.status
    normalized = canonicalize_url(input_url)
    llms_meta = {"available": True, "used": True, "url": probe.url}
    return {
        "input_url": input_url,
        "normalized_url": normalized,
        "fetched_url": probe.url,
        "status": status,
        "source_type": "llms_txt",
        "fetch_backend": "llms_txt",
        "origin_backend": "llms_txt",
        "cached": False,
        "content_type": probe.content_type or "text/plain",
        "markdown": content,
        "metadata": {"source": "llms.txt", "url": probe.url},
        "links": [],
        "error": (
            None
            if status == "success"
            else {
                "code": classification.reason or "partial",
                "message": classification.reason or "partial",
                "retryable": False,
            }
        ),
        "entities": None,
        "llms_txt": llms_meta,
        "diagnostics": None,
    }


def _artifact_from_content(input_url: str, fetched: ContentArtifact) -> dict[str, Any]:
    artifact = artifact_to_dict(fetched)
    artifact["input_url"] = input_url
    return artifact


def _artifact_from_exception(input_url: str, exc: Exception, *, timeout: bool = False) -> dict[str, Any]:
    normalized = canonicalize_url(input_url)
    if timeout:
        error = {
            "code": "timeout",
            "message": "Fetch exceeded the dsh-webfetch 20 second request budget.",
            "retryable": True,
        }
        backend = "timeout"
    else:
        error = _error_dict(exc)
        backend = "exception"
    return {
        "input_url": input_url,
        "normalized_url": normalized,
        "fetched_url": None,
        "status": "error",
        "source_type": "unknown",
        "fetch_backend": backend,
        "origin_backend": backend,
        "cached": False,
        "content_type": None,
        "markdown": "",
        "metadata": None,
        "links": None,
        "error": error,
        "entities": None,
        "llms_txt": None,
        "diagnostics": None,
    }


async def _fetch_one_artifact(
    input_url: str,
    *,
    fetch_options: FetchOptions,
    llms_probe: LlmsTxtResult | None = None,
) -> dict[str, Any]:
    normalized = canonicalize_url(input_url)
    cache_key = _cache_key(normalized)
    probe = llms_probe
    if probe is None:
        try:
            probe = await check_llms_txt(
                input_url,
                timeout_seconds=5.0,
                max_response_bytes=fetch_options.max_response_bytes,
            )
        except Exception:
            probe = LlmsTxtResult(available=False)
    if probe.available:
        artifact = _artifact_from_llms(input_url, probe)
        await _store_cache(artifact)
        return artifact

    try:
        cached = await get_page_cache().alookup(cache_key)
    except Exception as exc:  # pragma: no cover - cache isolation
        LOGGER.warning("Page cache lookup failed: %s", exc)
        cached = None
    if cached:
        return _artifact_from_cache(input_url, normalized, cached)

    try:
        fetched = await asyncio.wait_for(
            fetch_content_artifact(input_url, fetch_options=fetch_options),
            timeout=settings.web_fetch_timeout_seconds,
        )
    except asyncio.TimeoutError:
        return _artifact_from_exception(input_url, TimeoutError(), timeout=True)
    except Exception as exc:
        return _artifact_from_exception(input_url, exc)

    artifact = _apply_status_error_invariant(_artifact_from_content(input_url, fetched))
    if probe is not None and probe.url:
        artifact["llms_txt"] = {"available": False, "used": False, "url": probe.url}
    await _store_cache(artifact)
    return artifact


def _result_from_artifact(
    artifact: dict[str, Any],
    *,
    offset: int,
    max_chars: int,
    include_metadata: bool,
    include_links: bool,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    full_markdown = str(artifact.get("markdown") or "")
    windowed = slice_content(full_markdown, offset=max(0, offset), length=max_chars)
    fetched_url = artifact.get("fetched_url") or artifact.get("normalized_url")
    artifact = _apply_status_error_invariant(dict(artifact))
    error = artifact.get("error")
    return {
        "input_url": artifact["input_url"],
        "normalized_url": artifact["normalized_url"],
        "fetched_url": fetched_url,
        "status": artifact.get("status", "error"),
        "source_type": artifact.get("source_type", "unknown"),
        "fetch_backend": artifact.get("fetch_backend", "unknown"),
        "origin_backend": artifact.get("origin_backend"),
        "cached": bool(artifact.get("cached", False)),
        "page_content": windowed.content,
        "window": windowed.window.__dict__,
        "content_format": _content_format(
            str(artifact.get("source_type", "")), artifact.get("content_type")
        ),
        "content_type": artifact.get("content_type"),
        "metadata": artifact.get("metadata") if include_metadata else None,
        "links": artifact.get("links") if include_links else None,
        "continuation_notice": windowed.window.continuation_notice,
        "error": error,
        "entities": artifact.get("entities"),
        "summary": summary,
        "content_word_count": len(full_markdown.split()),
        "page_char_count": len(windowed.content),
        "word_count": len(windowed.content.split()),
        "wall": wall_from_classification(
            str(artifact.get("status", "")),
            error if isinstance(error, dict) else None,
            str(artifact.get("source_type") or "") or None,
            full_markdown,
        ),
        "llms_txt": artifact.get("llms_txt"),
        "diagnostics": artifact.get("diagnostics"),
    }


def _normalize_inputs(
    url: str | None,
    urls: list[str] | None,
    cursor: str | None,
) -> tuple[Literal["single", "bulk"], list[str], dict[str, Any] | None]:
    primary = url.strip() if isinstance(url, str) and url.strip() else None
    supplied_urls = [item.strip() for item in (urls or []) if isinstance(item, str) and item.strip()]
    if cursor:
        if primary or supplied_urls:
            raise_tool_error(
                ValueError("cursor cannot be combined with url or urls"), provider="fetch"
            )
        decoded = _decode_cursor(cursor)
        return "bulk", decoded["urls"], decoded
    if primary is None and not supplied_urls:
        raise_tool_error(ValueError("Provide url or a non-empty urls list"), provider="fetch")
    if primary is not None and not supplied_urls:
        return "single", [primary], None
    if primary is None:
        return "bulk", list(dict.fromkeys(supplied_urls)), None
    combined = list(dict.fromkeys([primary, *supplied_urls]))
    return ("bulk" if len(combined) > 1 else "single"), combined, None


async def fetch(
    url: str | None = None,
    urls: list[str] | None = None,
    offset: int = 0,
    cursor: str | None = None,
    ai_summary: bool = False,
    focus_query: str | None = None,
    include_metadata: bool = True,
    include_links: bool = False,
    max_links: int = 25,
    strip_selectors: str | None = None,
    ctx: Context = CurrentContext(),
) -> FetchResponse:
    """Fetch one URL or multiple URLs through the unified content pipeline.

    Content is returned in full by default. ``offset`` skips the first N characters
    and returns the remainder. Pipeline limits: 20s per-request timeout, 5 MiB
    response body. Bulk calls use fixed ten-item waves and bounded internal
    concurrency; those resource controls are intentionally not public arguments.
    """
    if offset < 0:
        raise_tool_error(ValueError("offset must be non-negative"), provider="fetch")
    if cursor and offset:
        raise_tool_error(
            ValueError("offset cannot be combined with a bulk cursor"), provider="fetch"
        )

    mode, pending_urls, cursor_payload = _normalize_inputs(url, urls, cursor)
    if mode == "bulk" and offset:
        raise_tool_error(
            ValueError("offset applies only to a single URL fetch"), provider="fetch"
        )

    workers = max(1, settings.web_fetch_workers)
    wave_size = max(1, settings.web_fetch_wave_size)
    fetch_options = build_fetch_options(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
        max_response_bytes=max(1, settings.web_fetch_max_body_bytes),
    )
    fingerprint = _request_fingerprint(
        fetch_options=fetch_options,
        focus_query=focus_query,
        ai_summary=ai_summary,
    )
    if cursor_payload and cursor_payload.get("fingerprint") != fingerprint:
        raise_tool_error(
            ValueError("Fetch cursor options do not match this request"), provider="fetch"
        )

    started = time.monotonic()
    emit_tool_observability_event(
        LOGGER,
        "fetch",
        "request",
        mode=mode,
        url_count=len(pending_urls),
        has_cursor=bool(cursor),
        ai_summary=ai_summary,
        focus_query=focus_query,
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )
    await ctx.info(f"Fetching {len(pending_urls)} URL(s) with the unified fetch tool...")

    if mode == "single":
        await ctx.report_progress(progress=20, total=100, message="Fetching URL...")
        artifact = await _fetch_one_artifact(pending_urls[0], fetch_options=fetch_options)
        result = _result_from_artifact(
            artifact,
            offset=offset,
            max_chars=0,
            include_metadata=include_metadata,
            include_links=include_links,
        )
        if ai_summary:
            result["summary"] = await create_summary(
                result["page_content"],
                ai_summary=True,
                focus_query=focus_query,
                source_urls=[result["fetched_url"]] if result.get("fetched_url") else None,
            )
            result["usage"] = TokenUsage.from_payload(result["summary"])
        try:
            validated = FetchResult.model_validate(result)
        except Exception as exc:
            raise_tool_error(
                ValueError(f"Invalid fetch result: {str(exc)[:200]}"), provider="fetch"
            )
        response = FetchResponse(
            mode="single",
            results=[validated],
            total_requested=1,
            total_returned=1,
            total_chars_returned=len(result["page_content"]),
            has_more=bool(result["window"].get("has_more")),
            cursor=None,
            wave_size=wave_size,
            waves_completed=1,
            duration_ms=int(round((time.monotonic() - started) * 1000.0)),
        )
        await ctx.report_progress(progress=100, total=100, message="Done")
    else:
        await ctx.report_progress(
            progress=5,
            total=100,
            message=f"Fetching {len(pending_urls)} URLs in waves of {wave_size}...",
        )
        semaphore = asyncio.Semaphore(workers)
        admitted: list[dict[str, Any]] = []
        deferred: list[str] = []
        waves_completed = 0

        async def _one(url_value: str) -> dict[str, Any]:
            async with semaphore:
                artifact = await _fetch_one_artifact(url_value, fetch_options=fetch_options)
                return _result_from_artifact(
                    artifact,
                    offset=0,
                    max_chars=0,
                    include_metadata=include_metadata,
                    include_links=include_links,
                )

        wave = pending_urls[:wave_size]
        wave_results = await asyncio.gather(*(_one(item) for item in wave))
        waves_completed = 1
        admitted.extend(wave_results)
        deferred = pending_urls[wave_size:]
        await ctx.report_progress(
            progress=min(95, 10 + int(85 * len(wave) / max(len(pending_urls), 1))),
            total=100,
            message=f"Fetched {len(wave)}/{len(pending_urls)} URLs...",
        )

        if ai_summary and admitted:
            summaries = await create_batch_summaries(
                admitted,
                ai_summary=True,
                focus_query=focus_query,
                max_concurrency=workers,
            )
            for index, summary in enumerate(summaries):
                admitted[index]["summary"] = summary
                admitted[index]["usage"] = TokenUsage.from_payload(summary)

        next_cursor = _encode_cursor(deferred, fingerprint) if deferred else None
        try:
            validated_results = [FetchResult.model_validate(item) for item in admitted]
        except Exception as exc:
            raise_tool_error(
                ValueError(f"Invalid fetch result: {str(exc)[:200]}"), provider="fetch"
            )
        response = FetchResponse(
            mode="bulk",
            results=validated_results,
            total_requested=len(pending_urls),
            total_returned=len(admitted),
            total_chars_returned=sum(len(item["page_content"]) for item in admitted),
            has_more=bool(deferred),
            cursor=next_cursor,
            wave_size=wave_size,
            waves_completed=waves_completed,
            duration_ms=int(round((time.monotonic() - started) * 1000.0)),
        )
        await ctx.report_progress(progress=100, total=100, message="Done")

    result_dict = response.model_dump(exclude_none=True)
    emit_tool_observability_event(
        LOGGER,
        "fetch",
        "response",
        duration_ms=(time.monotonic() - started) * 1000.0,
        mode=response.mode,
        url_count=response.total_requested,
        result_count=response.total_returned,
        total_chars_returned=response.total_chars_returned,
        has_more=response.has_more,
        cursor=response.cursor,
        results=result_dict.get("results"),
    )
    _record_tool_success(
        "fetch",
        input_url_count=response.total_requested,
        output_result_count=response.total_returned,
    )
    return response
