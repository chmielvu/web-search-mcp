from __future__ import annotations

import dataclasses

import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlparse

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from ..cache import get_page_cache
from ..content.ai_summary import summarize, summarize_batch
from ..content.constructor import (
    ContentArtifact,
    artifact_to_dict,
    finalize_artifact,
    rehydrate_cached_artifact,
)
from ..content.fetch_pipeline import fetch_content_artifact
from ..content.models import (
    PROCESSING_POLICY_VERSION,
    ContentError,
    FetchOptions,
)
from ..errors import raise_tool_error
from ..models import FetchError, FetchResponse, FetchResult, PublicStatus, TokenUsage
from ..settings import settings
from ..utils.observability import emit_tool_observability_event
from ..utils.text_chunking import slice_content
from ..utils.url_canonicalize import canonicalize_url
from ._helpers import _record_tool_failure, _record_tool_success

LOGGER = logging.getLogger(__name__)

_CURSOR_VERSION = 1
# Cache is keyed by normalized URL + policy version + processing mode; the
# envelope stores the complete artifact payload and a version stamp so any
# schema change forces a fresh fetch instead of replaying stale shapes.
_CACHE_POLICY_VERSION = PROCESSING_POLICY_VERSION

# ---------------------------------------------------------------------------
# Cache envelope (versioned; sole consumer of get_page_cache)
# ---------------------------------------------------------------------------


def _cache_key(normalized_url: str, *, processing_mode: str) -> str:
    """Identity-shaped cache key isolates mode+policy writes from each other."""
    return f"web-fetch:{_CACHE_POLICY_VERSION}:{processing_mode}:{normalized_url}"


def _cache_envelope(artifact: ContentArtifact) -> dict[str, Any]:
    """Wrap a finalized artifact for versioned round-trip through page_cache."""
    return {
        "policy_version": _CACHE_POLICY_VERSION,
        "processing_mode": artifact.processing_mode,
        "artifact": artifact_to_dict(artifact),
    }


async def _store_cache(artifact: ContentArtifact) -> None:
    """Persist the complete artifact envelope keyed by policy+mode+identity."""
    if artifact.status not in {"success", "partial"} or not artifact.markdown.strip():
        return
    try:
        await get_page_cache().astore(
            canonical_url=_cache_key(
                artifact.normalized_url, processing_mode=artifact.processing_mode
            ),
            page_content=artifact.markdown,
            extraction_method=artifact.fetch_backend or "cache",
            metadata=_cache_envelope(artifact),
        )
    except Exception as exc:  # pragma: no cover - cache isolation
        LOGGER.warning("Page cache store failed: %s", exc)


async def _lookup_cache(normalized_url: str, *, processing_mode: str) -> ContentArtifact | None:
    """Return a version-compatible cached artifact or ``None`` on miss/mismatch."""
    try:
        cached = await get_page_cache().alookup(
            _cache_key(normalized_url, processing_mode=processing_mode)
        )
    except Exception as exc:  # pragma: no cover - cache isolation
        LOGGER.warning("Page cache lookup failed: %s", exc)
        return None
    if not cached:
        return None
    envelope = (cached.get("metadata") or {}) if isinstance(cached, dict) else {}
    return rehydrate_cached_artifact(envelope, normalized_url)


# ---------------------------------------------------------------------------
# Single-URL orchestration boundary: cache lookup → pipeline → sole cache store
# ---------------------------------------------------------------------------


async def _fetch_one_artifact(
    input_url: str,
    *,
    fetch_options: FetchOptions,
    stage_attempts: list | None = None,
) -> ContentArtifact:
    """Return the sole finalized artifact for one URL via the candidate path."""
    normalized = canonicalize_url(input_url)

    cached = await _lookup_cache(normalized, processing_mode=fetch_options.processing_mode)
    if cached is not None:
        return cached

    artifact: ContentArtifact
    try:
        artifact = await asyncio.wait_for(
            fetch_content_artifact(
                input_url,
                fetch_options=fetch_options,
                stage_attempts=stage_attempts,
            ),
            timeout=settings.web_fetch_timeout_seconds,
        )
    except asyncio.TimeoutError:
        artifact = await finalize_artifact(
            input_url,
            None,
            options=fetch_options,
            failure=ContentError(
                "timeout",
                f"Fetch exceeded the {int(settings.web_fetch_timeout_seconds)} second request budget.",
                retryable=True,
                status="error",
            ),
            failure_status="error",
        )
    except Exception as exc:
        artifact = await finalize_artifact(
            input_url,
            None,
            options=fetch_options,
            failure=ContentError(
                type(exc).__name__,
                str(exc)[:500],
                retryable=False,
                status="error",
            ),
            failure_status="error",
        )

    await _store_cache(artifact)
    return artifact


# ---------------------------------------------------------------------------
# Error shaping
# ---------------------------------------------------------------------------


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


def _shape_fetch_error(artifact: ContentArtifact) -> dict[str, Any]:
    error = artifact.error
    raw_error: dict[str, Any] = (
        dataclasses.asdict(error) if error is not None else {}
    )
    code = str(raw_error.get("code") or artifact.status or "fetch_error")
    message = str(raw_error.get("message") or "The fetch could not complete.")
    retryable = bool(raw_error.get("retryable", False))
    raw_http_status = raw_error.get("http_status")
    http_status = (
        int(raw_http_status) if isinstance(raw_http_status, int) else _error_http_status(code)
    )
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
    stage = raw_error.get("stage") or artifact.fetch_backend
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


def _classify_status(
    artifact: ContentArtifact,
    raw_status: str,
    access_signal: str | None,
) -> tuple[PublicStatus, dict[str, Any] | None]:
    """Collapse internal status and wall classification into the public outcome."""
    if access_signal in {"login", "paywall", "bot", "js_shell"}:
        return cast(PublicStatus, access_signal), None
    if artifact.error is not None and raw_status in {"", "error"}:
        return "error", FetchError.model_validate(_shape_fetch_error(artifact)).model_dump(
            exclude_none=True
        )
    if raw_status in {"success", "partial", "blocked", "unsupported"}:
        return cast(PublicStatus, raw_status), None
    return "error", FetchError.model_validate(_shape_fetch_error(artifact)).model_dump(
        exclude_none=True
    )


def _access_signal_from_artifact(artifact: ContentArtifact) -> str | None:
    """Derive login/paywall/bot from finalizer evidence, not reclassification."""
    lowered_flags = {str(flag).lower() for flag in artifact.quality.flags}
    code = str(artifact.error.code).lower() if artifact.error else ""
    if "login_wall" in lowered_flags or code.startswith("login_wall"):
        return "login"
    if "paywall" in lowered_flags or code.startswith("paywall"):
        return "paywall"
    if (
        "access_blocked" in lowered_flags
        or "bot_challenge" in lowered_flags
        or code.startswith("access_blocked")
        or "captcha" in f"{lowered_flags} {code}"
        or "challenge" in f"{lowered_flags} {code}"
    ):
        return "bot"
    return None


def _public_links(artifact: ContentArtifact, content_window: str) -> list[dict[str, Any]] | None:
    """Return the structured links the finalizer captured; no regex fallback."""
    raw_links: list[dict[str, Any]] = []
    for entry in artifact.links or ():
        if isinstance(entry, dict):
            raw_links.append(entry)
    if not raw_links and not content_window:
        return None
    base_url = artifact.fetched_url or artifact.normalized_url
    source_domain = urlparse(base_url).netloc.lower() or None
    out: list[dict[str, Any]] = []
    for entry in raw_links:
        url = str(entry.get("url") or entry.get("href") or "").strip()
        if not url:
            continue
        domain = urlparse(url).netloc.lower() or None
        out.append(
            {
                "url": url,
                "text": str(entry.get("text") or entry.get("title") or ""),
                "domain": domain,
                "internal": bool(source_domain and domain == source_domain),
            }
        )
    return out or None


def _result_from_artifact(
    artifact: ContentArtifact,
    *,
    offset: int,
    max_chars: int,
    include_links: bool,
) -> dict[str, Any]:
    full_markdown = artifact.markdown or ""
    windowed = slice_content(full_markdown, offset=max(0, offset), length=max_chars)
    raw_status = (artifact.status or "").strip().lower()
    access_signal = _access_signal_from_artifact(artifact)
    public_status, error_obj = _classify_status(artifact, raw_status, access_signal)
    diagnostics_payload = (
        [dataclasses.asdict(d) for d in artifact.diagnostics]
        if artifact.diagnostics
        else None
    )
    entities_payload = None
    if artifact.entities is not None:
        entities_payload = [
            entity.model_dump(exclude_none=True) if hasattr(entity, "model_dump") else dict(entity)
            for entity in artifact.entities
        ]
    result = {
        "url": artifact.fetched_url or artifact.normalized_url,
        "status": public_status,
        "content": windowed.content,
        "error": error_obj,
        "links": _public_links(artifact, windowed.content) if include_links else None,
        "window": {
            "offset": windowed.window.offset,
            "length": windowed.window.length,
            "returned_chars": windowed.window.returned_chars,
            "total_chars": windowed.window.total_chars,
            "has_more": windowed.window.has_more,
            "next_offset": windowed.window.next_offset,
        },
        "entities": entities_payload,
        "diagnostics": diagnostics_payload,
        "output_path": artifact.output_path,
    }
    return result


def _analytics_wall(artifact: ContentArtifact) -> dict[str, object] | None:
    """Project access-wall metadata from finalizer evidence."""
    signal = _access_signal_from_artifact(artifact)
    if signal in {"login", "paywall", "bot"}:
        return {"kind": signal, "confidence": "high", "retryable": False}
    return None


def _analytics_payload(
    artifact: ContentArtifact,
    public_result: dict[str, Any],
) -> dict[str, Any]:
    """Project the complete finalizer payload for downstream analytics writers."""
    full_markdown = artifact.markdown or ""
    error_payload: dict[str, Any] | None = None
    if artifact.error is not None:
        error_payload = {
            "code": artifact.error.code,
            "category": _error_category(
                artifact.error.code,
                retryable=artifact.error.retryable,
                http_status=artifact.error.http_status,
            ),
            "message": artifact.error.message,
            "retryable": artifact.error.retryable,
            "http_status": artifact.error.http_status,
        }
    quality_payload = {
        "accepted": artifact.quality.accepted,
        "score": artifact.quality.score,
        "word_count": artifact.quality.word_count,
        "heading_count": artifact.quality.heading_count,
        "code_blocks": artifact.quality.code_blocks,
        "tables": artifact.quality.tables,
        "duplicate_ratio": artifact.quality.duplicate_ratio,
        "boilerplate_hits": artifact.quality.boilerplate_hits,
        "malformed_tables": artifact.quality.malformed_tables,
        "fence_errors": artifact.quality.fence_errors,
        "flags": list(artifact.quality.flags),
    }
    stage_path = []
    for transform in artifact.transforms or ():
        if transform and transform not in stage_path:
            stage_path.append(transform)
    selection_reason = artifact.transforms[0] if artifact.transforms else artifact.fetch_backend
    return {
        **public_result,
        "input_url": artifact.input_url,
        "normalized_url": artifact.normalized_url,
        "fetched_url": artifact.fetched_url or public_result.get("url"),
        "source_type": artifact.source_type,
        "fetch_backend": artifact.fetch_backend,
        "cached": bool(artifact.cached),
        "processing_mode": artifact.processing_mode,
        "policy_version": artifact.policy_version,
        "complete": artifact.complete,
        "scope": artifact.scope,
        "bytes_downloaded": artifact.bytes_downloaded,
        "redirect_count": artifact.redirect_count,
        "page_content": full_markdown,
        "content_type": artifact.content_type,
        "title": artifact.title,
        "metadata": artifact.metadata,
        "content_word_count": artifact.quality.word_count,
        "page_char_count": len(full_markdown),
        "word_count": artifact.quality.word_count,
        "quality": quality_payload,
        "selection_reason": selection_reason,
        "stage_path": " > ".join(stage_path) or None,
        "wall": _analytics_wall(artifact),
        "error": error_payload,
        "coverage": artifact.coverage,
    }


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


def _mark_summary_failure(
    result: dict[str, Any],
    message: str,
    *,
    analytics_result: dict[str, Any] | None = None,
) -> None:
    """Expose summary-generation failures without discarding fetched content."""
    if result.get("status") == "success":
        result["status"] = "partial"
    diagnostics = result.get("diagnostics")
    if not isinstance(diagnostics, list):
        diagnostics = []
        result["diagnostics"] = diagnostics
    diagnostics.append(
        {
            "code": "summary_failed",
            "message": message[:200],
            "retryable": False,
        }
    )
    if analytics_result is not None:
        analytics_result["status"] = result["status"]
        analytics_result["diagnostics"] = diagnostics


def _request_fingerprint(
    *,
    fetch_options: FetchOptions,
    focus_query: str | None,
    ai_summary: bool,
    processing_mode: str,
) -> str:
    payload = {
        "policy_version": _CACHE_POLICY_VERSION,
        "fetch_options": fetch_options.cache_fingerprint(),
        "focus_query": focus_query or "",
        "ai_summary": ai_summary,
        "processing_mode": processing_mode,
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
    url: Annotated[
        str | None,
        Field(description="One URL to fetch. Exactly one of url/urls/cursor must be supplied."),
    ] = None,
    urls: Annotated[
        list[str] | None,
        Field(
            description="URL list; exactly one of url/urls/cursor required; cursor pages the remainder beyond the first wave."
        ),
    ] = None,
    offset: Annotated[
        int,
        Field(
            ge=0,
            description="Skip the first N characters of a single-URL result; cannot combine with cursor or urls.",
        ),
    ] = 0,
    cursor: Annotated[
        str | None,
        Field(
            description="Opaque continuation from a previous bulk response's cursor field; mutually exclusive with url/urls/offset."
        ),
    ] = None,
    ai_summary: Annotated[
        bool,
        Field(
            description="Replace content with a Gemini source-grounded summary (default false = raw content)."
        ),
    ] = False,
    focus_query: Annotated[
        str | None, Field(description="Bias the ai_summary toward this topic or term.")
    ] = None,
    include_links: Annotated[
        bool, Field(description="Also extract outbound links (default false).")
    ] = False,
    processing_mode: Annotated[
        Literal["agent", "index"],
        Field(
            description="agent (default): ephemeral fetch for tool consumers; index: persist selected markdown under REPO_ROOT/outputs and surface output_path per result.",
        ),
    ] = "agent",
    ctx: Context = CurrentContext(),
) -> FetchResponse:
    """Fetch one URL or multiple URLs and return their extracted content.

    WHEN TO USE:
    - Reading the full text of URLs discovered by web_search, quick_web_search,
      gemini_search, code_search, or any other tool.
    - One-off URL content retrieval: articles, docs, GitHub
      issue/discussion/PR pages, and non-GitHub sources.
    - Bulk reading: pass a URL list in urls.
    - GitHub file contents: pass the raw.githubusercontent.com or github.com
      blob file URL directly.

    WHEN NOT TO USE:
    - Cross-repo discovery (use code_search).

    RETURNS:
    - results[]: one entry per URL, each with url, status ("success" or a typed
      failure like "blocked", "login", "paywall", "js_shell"), content, and
      error with category + resolution + retryable flag.
    - window: pagination metadata (offset, returned_chars, total_chars,
      has_more, next_offset) for a single-URL fetch.
    - cursor: continuation for bulk fetches; pass back to page through
      remaining URLs.
    - links[]: outbound links (when include_links=true).
    - output_path: populated per result when processing_mode="index".

    CHAINING:
    - Single URL: use offset to page through long content.
    - Bulk: pass cursor back to fetch the next wave of URLs.
    - ai_summary=true replaces content with a Gemini source-grounded summary;
      use focus_query to bias the summary.
    - processing_mode="index" persists the finalized markdown under
      REPO_ROOT/outputs and reports the absolute path per result.

    ERROR RECOVERY: each failed URL returns a typed FetchError (code,
    category, message, resolution, retryable). Act on resolution before
    retrying; retryable=true errors can be retried as-is.
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
        processing_mode=processing_mode,
    )
    fingerprint = _request_fingerprint(
        fetch_options=fetch_options,
        focus_query=focus_query,
        ai_summary=ai_summary,
        processing_mode=processing_mode,
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
        processing_mode=processing_mode,
    )
    await ctx.info(f"Fetching {len(pending_urls)} URL(s) with the unified fetch tool...")
    stage_attempts_all: list = []
    rung_log: list = []
    summary_failed = False
    if mode == "single":
        await ctx.report_progress(progress=20, total=100, message="Fetching URL...")
        stage_attempts: list = []
        artifact = await _fetch_one_artifact(
            pending_urls[0], fetch_options=fetch_options, stage_attempts=stage_attempts
        )
        result = _result_from_artifact(
            artifact,
            offset=offset,
            max_chars=0,
            include_links=include_links,
        )
        analytics_result = _analytics_payload(artifact, result)
        if ai_summary:
            try:
                summary_obj = await summarize(
                    result["content"],
                    ai_summary=True,
                    focus_query=focus_query,
                    source_urls=[result["url"]] if result.get("url") else None,
                    rung_log=rung_log,
                )
            except Exception as exc:
                LOGGER.warning("Optional summary failed for %s: %s", result.get("url"), exc)
                summary_failed = True
                _mark_summary_failure(
                    result,
                    f"Optional summary failed: {type(exc).__name__}: {exc}",
                    analytics_result=analytics_result,
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
                    else:
                        summary_failed = True
                        _mark_summary_failure(
                            result,
                            "Optional summary returned no usable text.",
                            analytics_result=analytics_result,
                        )
                    analytics_result["summary"] = summary_obj
                    usage = TokenUsage.from_payload(summary_obj)
                    if usage is not None:
                        analytics_result["usage"] = usage.model_dump(exclude_none=True)
                    analytics_result["content"] = result["content"]
                else:
                    summary_failed = True
                    _mark_summary_failure(
                        result,
                        "Optional summary returned no usable payload.",
                        analytics_result=analytics_result,
                    )
        analytics_result["status"] = result["status"]
        analytics_result["content"] = result["content"]
        try:
            validated = FetchResult.model_validate(result)
        except Exception as exc:
            raise_tool_error(
                ValueError(f"Invalid fetch result: {str(exc)[:200]}"), provider="fetch"
            )
        stage_attempts_all.extend(stage_attempts)
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

        async def _one(url_value: str) -> tuple[dict[str, Any], dict[str, Any], list]:
            stage_attempts: list = []
            async with semaphore:
                artifact = await _fetch_one_artifact(
                    url_value, fetch_options=fetch_options, stage_attempts=stage_attempts
                )
                result = _result_from_artifact(
                    artifact,
                    offset=0,
                    max_chars=0,
                    include_links=include_links,
                )
                return result, _analytics_payload(artifact, result), stage_attempts

        stage_attempts_all: list = []
        for wave_start in range(0, len(pending_urls), wave_size):
            wave = pending_urls[wave_start : wave_start + wave_size]
            wave_results = await asyncio.gather(*(_one(item) for item in wave))
            waves_completed += 1
            for result, analytics_result, item_stage_attempts in wave_results:
                admitted.append(result)
                analytics_admitted.append(analytics_result)
                for attempt in item_stage_attempts:
                    attempt["item_index"] = len(admitted) - 1
                stage_attempts_all.extend(item_stage_attempts)
        deferred = pending_urls[len(admitted) :]
        await ctx.report_progress(
            progress=min(95, 10 + int(85 * len(admitted) / max(len(pending_urls), 1))),
            total=100,
            message=f"Fetched {len(admitted)}/{len(pending_urls)} URLs...",
        )

        if ai_summary and admitted:
            summary_failure_message = "Optional summary returned no usable payload."
            try:
                summaries = await summarize_batch(
                    [_summary_input(item) for item in admitted],
                    ai_summary=True,
                    focus_query=focus_query,
                    rung_log=rung_log,
                )
            except Exception as exc:
                LOGGER.warning("Optional batch summaries failed: %s", exc)
                summary_failed = True
                summary_failure_message = (
                    f"Optional batch summary failed: {type(exc).__name__}: {exc}"
                )
                summaries = []
            for index in range(len(admitted)):
                summary = summaries[index] if index < len(summaries) else None
                if not isinstance(summary, dict):
                    summary_failed = True
                    _mark_summary_failure(
                        admitted[index],
                        summary_failure_message,
                        analytics_result=analytics_admitted[index],
                    )
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
                    else:
                        summary_failed = True
                        _mark_summary_failure(
                            admitted[index],
                            "Optional summary returned no usable text.",
                            analytics_result=analytics_admitted[index],
                        )
                    analytics_admitted[index]["content"] = admitted[index]["content"]
                    analytics_admitted[index]["status"] = admitted[index]["status"]
                except Exception as exc:
                    LOGGER.warning(
                        "Optional summary application failed for item %s: %s", index, exc
                    )
                    summary_failed = True
                    _mark_summary_failure(
                        admitted[index],
                        f"Optional summary failed: {type(exc).__name__}: {exc}",
                        analytics_result=analytics_admitted[index],
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
        processing_mode=processing_mode,
        results=analytics_results,
        stage_attempts=stage_attempts_all,
        summary_rungs=rung_log,
    )
    if summary_failed:
        _record_tool_failure("fetch")
    else:
        _record_tool_success(
            "fetch",
            input_url_count=response.total_requested,
            output_result_count=response.total_returned,
        )
    return response
