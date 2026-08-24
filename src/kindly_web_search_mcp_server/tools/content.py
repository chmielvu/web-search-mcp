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
from ..content.artifact import ContentArtifact
from ..content.fetch_pipeline import fetch_content_artifact
from ..content.llms_txt import LlmsTxtResult, check_llms_txt
from ..content.options import FetchOptions, build_fetch_options
from ..content.status_classifier import classify_markdown
from ..content.summary import create_batch_summaries, create_summary
from ..content.windowing import slice_content
from ..models import FetchResponse, FetchResult
from ..settings import settings
from ..utils.observability import emit_tool_observability_event
from ..utils.url_canonicalize import canonicalize_url
from ._helpers import _record_tool_success

LOGGER = logging.getLogger(__name__)

_CURSOR_VERSION = 1
_CACHE_SCHEMA_VERSION = 2
_CACHE_ROUTE_VERSION = 2
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


def _error_dict(exc: Exception, *, retryable: bool = True) -> dict[str, Any]:
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


def _wall_for(
    status: str,
    error: dict[str, Any] | None,
    markdown: str,
) -> dict[str, Any] | None:
    reason = " ".join(
        str(value).lower()
        for value in (
            (error or {}).get("code", ""),
            (error or {}).get("message", ""),
            markdown[:2000],
        )
    )
    if "paywall" in reason or "subscriber" in reason or "premium" in reason:
        return {"kind": "paywall", "confidence": "medium", "retryable": False}
    if "login_wall" in reason or "auth" in reason or "sign in" in reason or "log in" in reason:
        return {"kind": "login", "confidence": "medium", "retryable": False}
    if (
        "access_blocked" in reason
        or "cloudflare" in reason
        or "captcha" in reason
        or "checking your browser" in reason
        or "verify you are human" in reason
    ):
        return {"kind": "bot", "confidence": "medium", "retryable": status != "success"}
    if "spa_shell" in reason or "javascript" in reason:
        return {"kind": "js_shell", "confidence": "medium", "retryable": True}
    return None


def _request_fingerprint(
    *,
    fetch_options: FetchOptions,
    focus_query: str | None,
    ai_summary: bool,
    item_max_chars: int,
    total_char_budget: int,
) -> str:
    payload = {
        "fetch_options": fetch_options.cache_fingerprint(),
        "focus_query": focus_query or "",
        "ai_summary": ai_summary,
        "item_max_chars": item_max_chars,
        "total_char_budget": total_char_budget,
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
    except Exception as exc:
        raise ValueError("Invalid fetch cursor") from exc
    if not isinstance(decoded, dict) or decoded.get("version") != _CURSOR_VERSION:
        raise ValueError("Unsupported fetch cursor version")
    if decoded.get("mode") != "bulk" or not isinstance(decoded.get("urls"), list):
        raise ValueError("Invalid bulk fetch cursor")
    urls = [item.strip() for item in decoded["urls"] if isinstance(item, str) and item.strip()]
    if not urls:
        raise ValueError("Fetch cursor contains no pending URLs")
    decoded["urls"] = list(dict.fromkeys(urls))
    return decoded


def _artifact_dict(
    *,
    input_url: str,
    normalized_url: str,
    fetched_url: str | None,
    status: str,
    source_type: str,
    fetch_backend: str,
    origin_backend: str | None,
    cached: bool,
    content_type: str | None,
    markdown: str,
    metadata: dict[str, Any] | None,
    links: list[dict[str, Any]] | None,
    error: dict[str, Any] | None,
    entities: Any = None,
    llms_txt: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "input_url": input_url,
        "normalized_url": normalized_url,
        "fetched_url": fetched_url,
        "status": status,
        "source_type": source_type,
        "fetch_backend": fetch_backend,
        "origin_backend": origin_backend or fetch_backend,
        "cached": cached,
        "content_type": content_type,
        "markdown": markdown,
        "metadata": metadata,
        "links": links,
        "error": error,
        "entities": entities,
        "llms_txt": llms_txt,
        "diagnostics": diagnostics,
    }


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
    if legacy:
        diagnostics.append(
            {
                "code": "legacy_cache_entry",
                "cache_schema_version": envelope.get("schema_version", 0),
                "cache_route_version": envelope.get("route_version", 0),
            }
        )

    return _artifact_dict(
        input_url=input_url,
        normalized_url=str(envelope.get("normalized_url") or normalized_url),
        fetched_url=str(fetched_url) if fetched_url else None,
        status=str(status),
        source_type=str(source_type),
        fetch_backend="cache",
        origin_backend=str(origin_backend),
        cached=True,
        content_type=str(content_type) if content_type else None,
        markdown=str(cached.get("page_content") or ""),
        metadata=metadata if isinstance(metadata, dict) else None,
        links=links if isinstance(links, list) else None,
        error=None,
        llms_txt=envelope.get("llms_txt") if isinstance(envelope.get("llms_txt"), dict) else None,
        diagnostics=diagnostics,
    )


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
            "llms_txt": artifact.get("llms_txt"),
            "diagnostics": artifact.get("diagnostics"),
        },
        "metadata": artifact.get("metadata"),
        "links": artifact.get("links"),
        "origin_backend": artifact.get("origin_backend") or artifact.get("fetch_backend"),
        "status_code": 200,
    }


async def _store_cache(artifact: dict[str, Any]) -> None:
    if artifact.get("status") not in {"success", "partial"} or not artifact.get("markdown"):
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
    return _artifact_dict(
        input_url=input_url,
        normalized_url=normalized,
        fetched_url=probe.url,
        status=status,
        source_type="llms_txt",
        fetch_backend="llms_txt",
        origin_backend="llms_txt",
        cached=False,
        content_type=probe.content_type or "text/plain",
        markdown=content,
        metadata={"source": "llms.txt", "url": probe.url},
        links=[],
        error=None if status == "success" else {"code": classification.reason or "partial", "message": classification.reason or "partial", "retryable": False},
        llms_txt=llms_meta,
    )


def _artifact_from_content(input_url: str, fetched: ContentArtifact) -> dict[str, Any]:
    error = None
    if fetched.error is not None:
        error = {
            "code": fetched.error.code,
            "message": fetched.error.message,
            "retryable": fetched.error.retryable,
        }
    return _artifact_dict(
        input_url=input_url,
        normalized_url=fetched.normalized_url,
        fetched_url=fetched.fetched_url,
        status=fetched.status,
        source_type=fetched.source_type,
        fetch_backend=fetched.fetch_backend,
        origin_backend=fetched.origin_backend or fetched.fetch_backend,
        cached=fetched.cached,
        content_type=fetched.content_type,
        markdown=fetched.markdown,
        metadata=fetched.metadata,
        links=fetched.links,
        error=error,
        entities=fetched.entities,
        diagnostics=fetched.diagnostics,
    )


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
    return _artifact_dict(
        input_url=input_url,
        normalized_url=normalized,
        fetched_url=None,
        status="error",
        source_type="unknown",
        fetch_backend=backend,
        origin_backend=backend,
        cached=False,
        content_type=None,
        markdown="",
        metadata=None,
        links=None,
        error=error,
    )


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

    artifact = _artifact_from_content(input_url, fetched)
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
    error = artifact.get("error")
    return {
        "input_url": artifact["input_url"],
        "normalized_url": artifact["normalized_url"],
        "url": fetched_url,
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
        "content_quality": artifact.get("status"),
        "content_word_count": len(full_markdown.split()),
        "page_char_count": len(windowed.content),
        "word_count": len(windowed.content.split()),
        "wall": _wall_for(str(artifact.get("status", "")), error, full_markdown),
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
            raise ValueError("cursor cannot be combined with url or urls")
        decoded = _decode_cursor(cursor)
        return "bulk", decoded["urls"], decoded
    if primary is None and not supplied_urls:
        raise ValueError("Provide url or a non-empty urls list")
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

    The tool uses dsh-webfetch defaults internally: 60k characters per item,
    20s per-request timeout, and a 5 MiB response-body limit. Bulk calls use
    fixed ten-item waves and bounded internal concurrency; those resource
    controls are intentionally not public arguments.
    """
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if cursor and offset:
        raise ValueError("offset cannot be combined with a bulk cursor")

    mode, pending_urls, cursor_payload = _normalize_inputs(url, urls, cursor)
    if mode == "bulk" and offset:
        raise ValueError("offset applies only to a single URL fetch")

    # 0 means unlimited (no truncation) - preserve pagination via offset/cursor when explicitly used
    item_max_chars = settings.web_fetch_item_max_chars
    total_char_budget = settings.web_fetch_total_char_budget
    # Normalize: 0 = unlimited, otherwise clamp to at least 1
    if item_max_chars > 0:
        item_max_chars = max(1, item_max_chars)
    if total_char_budget > 0:
        total_char_budget = max(1, total_char_budget)
        # Ensure total budget at least covers one item when both are limited
        if item_max_chars > 0:
            total_char_budget = max(item_max_chars, total_char_budget)
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
        item_max_chars=item_max_chars,
        total_char_budget=total_char_budget,
    )
    if cursor_payload and cursor_payload.get("fingerprint") != fingerprint:
        raise ValueError("Fetch cursor options do not match this request")

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
            max_chars=item_max_chars,
            include_metadata=include_metadata,
            include_links=include_links,
        )
        if ai_summary:
            result["summary"] = await create_summary(
                result["page_content"],
                ai_summary=True,
                focus_query=focus_query,
                source_urls=[result["url"]] if result.get("url") else None,
            )
        response = FetchResponse(
            mode="single",
            results=[FetchResult.model_validate(result)],
            total_requested=1,
            total_returned=1,
            total_chars_returned=len(result["page_content"]),
            has_more=bool(result["window"].get("has_more")),
            cursor=None,
            wave_size=wave_size,
            waves_completed=1,
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
        # 0 total budget means unlimited - no char deferral
        remaining_budget = total_char_budget if total_char_budget > 0 else None
        waves_completed = 0

        async def _one(url_value: str) -> dict[str, Any]:
            async with semaphore:
                artifact = await _fetch_one_artifact(url_value, fetch_options=fetch_options)
                return _result_from_artifact(
                    artifact,
                    offset=0,
                    max_chars=item_max_chars,
                    include_metadata=include_metadata,
                    include_links=include_links,
                )

        for wave_start in range(0, len(pending_urls), wave_size):
            wave = pending_urls[wave_start : wave_start + wave_size]
            wave_results = await asyncio.gather(*(_one(item) for item in wave))
            waves_completed += 1
            for index, item in enumerate(wave_results):
                # Only enforce budget when it's limited (>0)
                if remaining_budget is not None:
                    chars_used = len(item["page_content"])
                    if chars_used > remaining_budget:
                        deferred = pending_urls[wave_start + index :]
                        break
                    admitted.append(item)
                    remaining_budget -= chars_used
                else:
                    admitted.append(item)
            await ctx.report_progress(
                progress=min(95, 10 + int(85 * (wave_start + len(wave)) / len(pending_urls))),
                total=100,
                message=f"Fetched {min(wave_start + len(wave), len(pending_urls))}/{len(pending_urls)} URLs...",
            )
            if deferred:
                break
            if wave_start + wave_size < len(pending_urls):
                await asyncio.sleep(max(0.0, settings.web_fetch_wave_delay_seconds))

        if ai_summary and admitted:
            summaries = await create_batch_summaries(
                admitted,
                ai_summary=True,
                focus_query=focus_query,
                max_concurrency=workers,
            )
            for index, summary in enumerate(summaries):
                admitted[index]["summary"] = summary

        next_cursor = _encode_cursor(deferred, fingerprint) if deferred else None
        response = FetchResponse(
            mode="bulk",
            results=[FetchResult.model_validate(item) for item in admitted],
            total_requested=len(pending_urls),
            total_returned=len(admitted),
            total_chars_returned=sum(len(item["page_content"]) for item in admitted),
            has_more=bool(deferred),
            cursor=next_cursor,
            wave_size=wave_size,
            waves_completed=waves_completed,
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
