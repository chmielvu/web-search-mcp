from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import Annotated, Any, Literal, cast

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from ..cache import get_page_cache
from ..content.artifact import ContentArtifact, artifact_to_dict
from ..content.ai_summary import create_batch_summaries, create_summary
from ..errors import raise_tool_error
from ..content.fetch_pipeline import fetch_content_artifact
from ..content.llms_txt import LlmsTxtResult, check_llms_txt
from ..content.fetch_pipeline import FetchOptions
from ..utils.content_classify import (
    ClassificationResult,
    classify_markdown,
    wall_from_classification,
)
from ..content.typed_content import SUPPORTED_TYPED_FORMATS
from ..models import FetchError, FetchResponse, FetchResult, PublicStatus, TokenUsage
from ..settings import settings
from ..utils.text_chunking import slice_content
from ..utils.observability import emit_tool_observability_event
from ..utils.url_canonicalize import canonicalize_url
from ._helpers import _record_tool_success

LOGGER = logging.getLogger(__name__)

_CURSOR_VERSION = 1
_CACHE_SCHEMA_VERSION = 4
_CACHE_ROUTE_VERSION = 4


def _cache_key(normalized_url: str) -> str:
    """Isolate fetch results from caches created under older route rules."""
    return f"web-fetch-route-v{_CACHE_ROUTE_VERSION}:{normalized_url}"


def _error_dict(exc: Exception) -> dict[str, Any]:
    retryable = isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError))
    return {
        "code": type(exc).__name__,
        "message": str(exc)[:500],
        "retryable": retryable,
    }


# Internal: cache envelope only; public FetchResult omits content format.
def _content_format(source_type: str, content_type: str | None) -> str:
    lowered = (content_type or "").split(";", 1)[0].strip().lower()
    if source_type in SUPPORTED_TYPED_FORMATS or source_type == "llms_txt":
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


def _artifact_from_cache(
    input_url: str, normalized_url: str, cached: dict[str, Any]
) -> dict[str, Any]:
    stored = cached.get("metadata")
    stored = stored if isinstance(stored, dict) else {}
    envelope = stored.get("__web_fetch__")
    envelope = envelope if isinstance(envelope, dict) else {}
    legacy = (
        not envelope
        or envelope.get("schema_version") != _CACHE_SCHEMA_VERSION
        or envelope.get("route_version") != _CACHE_ROUTE_VERSION
    )

    metadata = (
        envelope.get("metadata")
        if isinstance(envelope.get("metadata"), dict)
        else stored.get("metadata")
    )
    links = (
        envelope.get("links") if isinstance(envelope.get("links"), list) else stored.get("links")
    )
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
            "format": _content_format(
                str(artifact.get("source_type", "")), artifact.get("content_type")
            ),
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
            extraction_method=artifact.get("origin_backend")
            or artifact.get("fetch_backend")
            or "unknown",
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


def _artifact_from_exception(
    input_url: str, exc: Exception, *, timeout: bool = False
) -> dict[str, Any]:
    normalized = canonicalize_url(input_url)
    if timeout:
        error = {
            "code": "timeout",
            "message": f"Fetch exceeded the {int(settings.web_fetch_timeout_seconds)} second request budget.",
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


def _error_http_status(code: str) -> int | None:
    prefix, _, value = code.lower().partition("_")
    if prefix != "http" or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if 100 <= parsed <= 599 else None


def _error_category(
    code: str,
    *,
    retryable: bool,
    http_status: int | None,
) -> Literal["validation", "auth", "rate_limit", "upstream", "blocked", "timeout", "internal"]:
    lowered = code.lower()
    if lowered in {"timeout", "request_timeout"} or "timeout" in lowered:
        return "timeout"
    if http_status == 429 or any(
        marker in lowered for marker in ("rate_limit", "rate-limit", "too_many", "quota")
    ):
        return "rate_limit"
    if http_status in {401, 407} or any(
        marker in lowered for marker in ("auth", "login", "unauthorized", "credential")
    ):
        return "auth"
    if http_status in {403, 451} or any(
        marker in lowered for marker in ("blocked", "captcha", "forbidden", "access_denied")
    ):
        return "blocked"
    if lowered in {"invalid_url", "invalidurl", "validation", "unsupported_url"}:
        return "validation"
    if lowered in {"internal", "internal_error"}:
        return "internal"
    del retryable
    return "upstream"


def _error_resolution(
    code: str,
    category: str,
    *,
    retryable: bool,
    http_status: int | None,
) -> str:
    if http_status == 404:
        return "Verify the URL or choose another source."
    if category == "validation":
        return "Provide a valid public http(s) URL and try again."
    if category == "auth":
        return "Authenticate with the source or choose a publicly accessible URL."
    if category == "rate_limit":
        return "Wait for the provider limit to clear, then retry the request."
    if category == "blocked":
        return "Choose another source or retry without triggering the source protection."
    if category == "timeout" or retryable:
        return "Retry the request; if it persists, choose another source."
    if code.lower() in {"unsupported", "unsupported_url"}:
        return "Choose a publicly supported http(s) URL."
    if category == "internal":
        return "Retry the request; report the error if it persists."
    return "Verify the source and retry the request."


def _shape_fetch_error(artifact: dict[str, Any]) -> dict[str, Any]:
    raw_error = artifact.get("error")
    raw_error = raw_error if isinstance(raw_error, dict) else {}
    code = str(raw_error.get("code") or artifact.get("status") or "fetch_error")
    message = str(raw_error.get("message") or "The fetch could not complete.")
    retryable = bool(raw_error.get("retryable", False))
    raw_http_status = raw_error.get("http_status")
    http_status = raw_http_status if isinstance(raw_http_status, int) else _error_http_status(code)
    category = _error_category(code, retryable=retryable, http_status=http_status)
    expected_format = raw_error.get("expected_format")
    if not isinstance(expected_format, dict) and category == "validation":
        expected_format = {
            "description": "A public HTTP(S) URL.",
            "example": "https://example.com",
            "pattern": "^https?://",
        }
    resolution = raw_error.get("resolution")
    if not isinstance(resolution, str) or not resolution.strip():
        resolution = _error_resolution(
            code,
            category,
            retryable=retryable,
            http_status=http_status,
        )
    stage = raw_error.get("stage") or artifact.get("fetch_backend")
    shaped: dict[str, Any] = {
        "code": code,
        "category": category,
        "message": message,
        "expected_format": expected_format,
        "resolution": resolution,
        "retryable": retryable,
        "http_status": http_status,
        "stage": str(stage) if stage else None,
    }
    return {key: value for key, value in shaped.items() if value is not None}


def _access_signal_from_artifact(
    artifact: dict[str, Any],
    classified: ClassificationResult,
) -> str | None:
    wall = wall_from_classification(
        classified,
        artifact.get("error") if isinstance(artifact.get("error"), dict) else None,
    )
    if isinstance(wall, dict):
        kind = wall.get("kind")
        if kind in {"login", "paywall", "bot", "js_shell"}:
            return cast(str, kind)
    return None


def _classify_status(
    artifact: dict[str, Any],
    raw_status: str,
    access_signal: str | None,
) -> tuple[PublicStatus, dict[str, Any] | None]:
    """Collapse internal status and wall classification into the public outcome."""
    if access_signal in {"login", "paywall", "bot", "js_shell"}:
        return cast(PublicStatus, access_signal), None
    raw_error = artifact.get("error")
    if isinstance(raw_error, dict) and raw_status in {"", "error"}:
        return "error", FetchError.model_validate(_shape_fetch_error(artifact)).model_dump(
            exclude_none=True
        )
    if raw_status in {"success", "partial", "blocked", "unsupported"}:
        return cast(PublicStatus, raw_status), None
    return "error", FetchError.model_validate(_shape_fetch_error(artifact)).model_dump(
        exclude_none=True
    )


def _result_from_artifact(
    artifact: dict[str, Any],
    *,
    offset: int,
    max_chars: int,
    include_links: bool,
) -> tuple[dict[str, Any], ClassificationResult]:
    artifact = _apply_status_error_invariant(dict(artifact))
    full_markdown = str(artifact.get("markdown") or "")
    windowed = slice_content(full_markdown, offset=max(0, offset), length=max_chars)
    raw_status = str(artifact.get("status") or "").strip().lower()
    classified = classify_markdown(
        full_markdown,
        source_type=artifact.get("source_type") or None,
    )
    access_signal = _access_signal_from_artifact(artifact, classified)
    public_status, error_obj = _classify_status(artifact, raw_status, access_signal)
    result = {
        "url": artifact.get("fetched_url") or artifact.get("normalized_url") or "",
        "status": public_status,
        "content": windowed.content,
        "error": error_obj,
        "links": artifact.get("links") if include_links else None,
        "window": {
            "offset": windowed.window.offset,
            "length": windowed.window.length,
            "returned_chars": windowed.window.returned_chars,
            "total_chars": windowed.window.total_chars,
            "has_more": windowed.window.has_more,
            "next_offset": windowed.window.next_offset,
        },
        "entities": artifact.get("entities"),
        "diagnostics": artifact.get("diagnostics"),
    }
    return result, classified


def _analytics_result(
    artifact: dict[str, Any],
    public_result: dict[str, Any],
    classified: ClassificationResult,
) -> dict[str, Any]:
    full_markdown = str(artifact.get("markdown") or "")
    internal = dict(public_result)
    internal.update(
        {
            "input_url": artifact.get("input_url"),
            "normalized_url": artifact.get("normalized_url"),
            "fetched_url": artifact.get("fetched_url") or public_result.get("url"),
            "source_type": artifact.get("source_type"),
            "fetch_backend": artifact.get("fetch_backend"),
            "origin_backend": artifact.get("origin_backend"),
            "cached": bool(artifact.get("cached", False)),
            "page_content": full_markdown,
            "content_format": _content_format(
                str(artifact.get("source_type", "")), artifact.get("content_type")
            ),
            "content_type": artifact.get("content_type"),
            "metadata": artifact.get("metadata"),
            "content_word_count": len(full_markdown.split()),
            "page_char_count": len(public_result.get("content") or ""),
            "word_count": len(str(public_result.get("content") or "").split()),
            "wall": wall_from_classification(
                classified,
                artifact.get("error") if isinstance(artifact.get("error"), dict) else None,
            ),
            "llms_txt": artifact.get("llms_txt"),
        }
    )
    return internal


def _summary_input(item: dict[str, Any]) -> dict[str, Any]:
    """Adapt the public result to the summary backend's internal input contract."""
    url = str(item.get("url") or "")
    return {
        **item,
        "input_url": url,
        "normalized_url": url,
        "fetched_url": url,
        "page_content": str(item.get("content") or ""),
    }


def _normalize_inputs(
    url: str | None,
    urls: list[str] | None,
    cursor: str | None,
) -> tuple[Literal["single", "bulk"], list[str], dict[str, Any] | None]:
    primary = url.strip() if isinstance(url, str) and url.strip() else None
    supplied_urls = [
        item.strip() for item in (urls or []) if isinstance(item, str) and item.strip()
    ]
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
    url: Annotated[str | None, Field(description="One URL to fetch. Exactly one of url/urls/cursor must be supplied.")] = None,
    urls: Annotated[list[str] | None, Field(description="URL list; exactly one of url/urls/cursor required; cursor pages the remainder beyond the first wave.")] = None,
    offset: Annotated[int, Field(ge=0, description="Skip the first N characters of a single-URL result; cannot combine with cursor or urls.")] = 0,
    cursor: Annotated[str | None, Field(description="Opaque continuation from a previous bulk response's cursor field; mutually exclusive with url/urls/offset.")] = None,
    ai_summary: Annotated[bool, Field(description="Replace content with a Gemini source-grounded summary (default false = raw content).")] = False,
    focus_query: Annotated[str | None, Field(description="Bias the ai_summary toward this topic or term.")] = None,
    include_links: Annotated[bool, Field(description="Also extract outbound links (default false).")] = False,
    ctx: Context = CurrentContext(),
) -> FetchResponse:
    """Fetch one URL or multiple URLs through the unified content pipeline.

    Content is returned in full by default. ``offset`` skips the first N characters
    and returns the remainder. Pipeline limits: 20s per-request timeout, 5 MiB
    response body. Bulk calls use fixed ten-item waves and bounded internal
    concurrency; those resource controls are intentionally not public arguments.

    For GitHub repository work (repo-wide search, line-anchored reads, file
    trees, symbol graphs) prefer code_fetch; fetch is for one-off URL content,
    GitHub issue/discussion/PR pages, and non-GitHub sources.
    """
    if offset < 0:
        raise_tool_error(ValueError("offset must be non-negative"), provider="fetch")
    if cursor and offset:
        raise_tool_error(
            ValueError("offset cannot be combined with a bulk cursor"), provider="fetch"
        )

    mode, pending_urls, cursor_payload = _normalize_inputs(url, urls, cursor)
    if mode == "bulk" and offset:
        raise_tool_error(ValueError("offset applies only to a single URL fetch"), provider="fetch")

    workers = max(1, settings.web_fetch_workers)
    wave_size = max(1, settings.web_fetch_wave_size)
    fetch_options = FetchOptions(
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
        include_links=include_links,
    )
    await ctx.info(f"Fetching {len(pending_urls)} URL(s) with the unified fetch tool...")

    analytics_results: list[dict[str, Any]] = []
    if mode == "single":
        await ctx.report_progress(progress=20, total=100, message="Fetching URL...")
        artifact = await _fetch_one_artifact(pending_urls[0], fetch_options=fetch_options)
        result, classified = _result_from_artifact(
            artifact,
            offset=offset,
            max_chars=0,
            include_links=include_links,
        )
        analytics_result = _analytics_result(artifact, result, classified)
        if ai_summary:
            try:
                summary_obj = await create_summary(
                    result["content"],
                    ai_summary=True,
                    focus_query=focus_query,
                    source_urls=[result["url"]] if result.get("url") else None,
                )
            except Exception as exc:
                LOGGER.warning("Optional summary failed for %s: %s", result.get("url"), exc)
                if result.get("diagnostics") is None:
                    result["diagnostics"] = []
                result["diagnostics"].append(
                    {
                        "code": "summary_failed",
                        "message": f"Optional summary failed: {type(exc).__name__}: {exc}"[:200],
                        "retryable": False,
                    }
                )
            else:
                if isinstance(summary_obj, dict):
                    summary_text = str(summary_obj.get("summary") or "").strip()
                    if summary_text:
                        result["content"] = summary_text
                        result["window"] = {
                            "offset": 0,
                            "length": len(summary_text),
                            "returned_chars": len(summary_text),
                            "total_chars": len(summary_text),
                            "has_more": False,
                            "next_offset": None,
                        }
                    analytics_result["summary"] = summary_obj
                    usage = TokenUsage.from_payload(summary_obj)
                    if usage is not None:
                        analytics_result["usage"] = usage.model_dump(exclude_none=True)
                    analytics_result["content"] = result["content"]
        try:
            validated = FetchResult.model_validate(result)
        except Exception as exc:
            raise_tool_error(
                ValueError(f"Invalid fetch result: {str(exc)[:200]}"), provider="fetch"
            )
        analytics_results = [analytics_result]
        response = FetchResponse(
            mode="single",
            results=[validated],
            total_requested=1,
            total_returned=1,
            total_chars_returned=len(result["content"]),
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
        analytics_admitted: list[dict[str, Any]] = []
        deferred: list[str] = []
        waves_completed = 0

        async def _one(url_value: str) -> tuple[dict[str, Any], dict[str, Any]]:
            async with semaphore:
                artifact = await _fetch_one_artifact(url_value, fetch_options=fetch_options)
                result, classified = _result_from_artifact(
                    artifact,
                    offset=0,
                    max_chars=0,
                    include_links=include_links,
                )
                return result, _analytics_result(artifact, result, classified)

        wave = pending_urls[:wave_size]
        wave_results = await asyncio.gather(*(_one(item) for item in wave))
        waves_completed = 1
        for public_result, analytics_result in wave_results:
            admitted.append(public_result)
            analytics_admitted.append(analytics_result)
        deferred = pending_urls[wave_size:]
        await ctx.report_progress(
            progress=min(95, 10 + int(85 * len(wave) / max(len(pending_urls), 1))),
            total=100,
            message=f"Fetched {len(wave)}/{len(pending_urls)} URLs...",
        )

        if ai_summary and admitted:
            try:
                summaries = await create_batch_summaries(
                    [_summary_input(item) for item in admitted],
                    ai_summary=True,
                    focus_query=focus_query,
                    max_concurrency=workers,
                )
            except Exception as exc:
                LOGGER.warning("Optional batch summaries failed: %s", exc)
                summaries = []
            for index, summary in enumerate(summaries):
                if not isinstance(summary, dict):
                    continue
                try:
                    analytics_admitted[index]["summary"] = summary
                    usage = TokenUsage.from_payload(summary)
                    if usage is not None:
                        analytics_admitted[index]["usage"] = usage.model_dump(exclude_none=True)
                    summary_text = str(summary.get("summary") or "").strip()
                    if summary_text:
                        admitted[index]["content"] = summary_text
                        admitted[index]["window"] = {
                            "offset": 0,
                            "length": len(summary_text),
                            "returned_chars": len(summary_text),
                            "total_chars": len(summary_text),
                            "has_more": False,
                            "next_offset": None,
                        }
                    analytics_admitted[index]["content"] = admitted[index]["content"]
                except Exception as exc:
                    LOGGER.warning("Optional summary application failed for item %s: %s", index, exc)
                    if admitted[index].get("diagnostics") is None:
                        admitted[index]["diagnostics"] = []
                    admitted[index]["diagnostics"].append(
                        {
                            "code": "summary_failed",
                            "message": f"Optional summary failed: {type(exc).__name__}: {exc}"[:200],
                            "retryable": False,
                        }
                    )

        next_cursor = _encode_cursor(deferred, fingerprint) if deferred else None
        try:
            validated_results = [FetchResult.model_validate(item) for item in admitted]
        except Exception as exc:
            raise_tool_error(
                ValueError(f"Invalid fetch result: {str(exc)[:200]}"), provider="fetch"
            )
        analytics_results = analytics_admitted
        response = FetchResponse(
            mode="bulk",
            results=validated_results,
            total_requested=len(pending_urls),
            total_returned=len(admitted),
            total_chars_returned=sum(len(item["content"]) for item in admitted),
            has_more=bool(deferred),
            cursor=next_cursor,
            wave_size=wave_size,
            waves_completed=waves_completed,
            duration_ms=int(round((time.monotonic() - started) * 1000.0)),
        )
        await ctx.report_progress(progress=100, total=100, message="Done")

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
        results=analytics_results,
    )
    _record_tool_success(
        "fetch",
        input_url_count=response.total_requested,
        output_result_count=response.total_returned,
    )
    return response
