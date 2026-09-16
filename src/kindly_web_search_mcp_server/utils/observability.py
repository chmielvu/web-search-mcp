"""Pure serialization / context helpers for observability events.

This module is intentionally dependency-free of the typed event-sink
package: the per-tool call persistence helpers and the public emitters
live in that other package. What remains here:

- Context variables: ``set_current_run_key`` / ``get_current_run_key``,
  ``set_current_tool_call_id`` / ``get_current_tool_call_id``
- Trace context extraction: :func:`current_trace_context`
- Serialization helpers: :func:`preview_text`,
  :func:`_normalize_for_body`,
  :func:`_normalize_for_analytics`,
  :func:`_normalize_for_extra`,
  :func:`_record_key`, :func:`_stable_hash`,
  :func:`serialize_search_results`, :func:`serialize_tool_links`,
  :func:`serialize_tool_metadata`, :func:`serialize_tool_event_fields`
- The tool request fingerprint helper consumed by
  :func:`serialize_tool_event_fields`

The public emitters (:func:`emit_observability_event`,
:func:`emit_tool_observability_event`) and the per-tool call sink have
moved to the dedicated producers package.
"""

from __future__ import annotations

import contextvars
import json
from hashlib import sha256
from typing import Any

try:
    from opentelemetry import trace
except Exception:  # pragma: no cover - optional observability dependency
    trace = None  # type: ignore[assignment]

from .environment import get_int_env

# Context variable to store run_key for the current search pipeline
_run_key_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_run_key_context", default=None
)
_tool_call_id_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_tool_call_id_context", default=None
)


def set_current_run_key(run_key: str | None) -> None:
    """Set the run_key for the current context."""
    _run_key_context.set(run_key)


def get_current_run_key() -> str | None:
    """Get the run_key for the current context."""
    return _run_key_context.get()


def set_current_tool_call_id(tool_call_id: str | None) -> None:
    """Set the stable lifecycle identifier for the current tool invocation."""
    _tool_call_id_context.set(tool_call_id)


def get_current_tool_call_id() -> str | None:
    """Get the stable lifecycle identifier for the current tool invocation."""
    return _tool_call_id_context.get()


_DEFAULT_MAX_TEXT_CHARS = 20000
_DEFAULT_MAX_ITEMS = 10
_DEFAULT_PREVIEW_CHARS = 2000


def _max_text_chars() -> int:
    return get_int_env("OBSERVABILITY_MAX_TEXT_CHARS", _DEFAULT_MAX_TEXT_CHARS)


def _max_items() -> int:
    return get_int_env("OBSERVABILITY_MAX_ITEMS", _DEFAULT_MAX_ITEMS)


def preview_text(value: str | None, *, limit: int | None = None) -> str:
    if not value:
        return ""
    hard_limit = limit or get_int_env("OBSERVABILITY_PREVIEW_CHARS", _DEFAULT_PREVIEW_CHARS)
    if len(value) <= hard_limit:
        return value
    return value[:hard_limit].rstrip() + "…"


def _normalize_for_body(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return preview_text(value, limit=_max_text_chars())
    if hasattr(value, "model_dump"):
        return _normalize_for_body(value.model_dump())
    if isinstance(value, dict):
        items = list(value.items())[: _max_items()]
        return {str(k): _normalize_for_body(v) for k, v in items}
    if isinstance(value, (list, tuple, set)):
        values = list(value)[: _max_items()]
        return [_normalize_for_body(item) for item in values]
    return preview_text(str(value), limit=_max_text_chars())


def _normalize_for_analytics(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "model_dump"):
        return _normalize_for_analytics(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _normalize_for_analytics(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_for_analytics(item) for item in value]
    return str(value)


def _normalize_for_extra(value: Any) -> str | bool | int | float | None:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return preview_text(value, limit=_max_text_chars())
    return json.dumps(_normalize_for_body(value), ensure_ascii=True, sort_keys=True)


def _record_key(name: str) -> str:
    normalized = []
    for char in name:
        normalized.append(char if char.isalnum() else "_")
    key = "".join(normalized).strip("_")
    return f"obs_{key}" if key else "obs_field"


def _stable_hash(value: Any, *, length: int = 16) -> str:
    normalized = _normalize_for_body(value)
    raw = json.dumps(normalized, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return sha256(raw).hexdigest()[:length]


def current_trace_context() -> dict[str, str]:
    if trace is None:
        return {}
    span = trace.get_current_span()
    context = span.get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }


def serialize_search_results(
    results: list[Any], *, max_results: int | None = None
) -> list[dict[str, Any]]:
    limit = max_results or _max_items()
    serialized: list[dict[str, Any]] = []
    for result in results[:limit]:
        if isinstance(result, dict):
            title = str(result.get("title") or "")
            link = str(result.get("link") or "")
            snippet = str(result.get("snippet") or "")
            domain = str(result.get("domain") or "")
            providers = list(result.get("providers") or [])
            score = result.get("score")
        else:
            title = getattr(result, "title", "") or ""
            link = getattr(result, "link", "") or ""
            snippet = getattr(result, "snippet", "") or ""
            domain = getattr(result, "domain", "")
            providers = list(getattr(result, "providers", []) or [])
            score = getattr(result, "score", None)
        serialized.append(
            {
                "title": preview_text(title, limit=1000),
                "link": link,
                "snippet": preview_text(snippet, limit=4000),
                "domain": domain,
                "providers": providers,
                "score": score,
                "title_len": len(title),
                "snippet_len": len(snippet),
                "link_hash": _stable_hash(link),
                "result_hash": _stable_hash(
                    {
                        "title": title,
                        "link": link,
                        "snippet": snippet,
                        "providers": providers,
                    }
                ),
            }
        )
    return serialized


def serialize_tool_links(
    links: list[Any] | None, *, max_links: int | None = None
) -> list[dict[str, Any]]:
    if not links:
        return []
    limit = max_links or _max_items()
    serialized: list[dict[str, Any]] = []
    for link in links[:limit]:
        if isinstance(link, dict):
            raw = dict(link)
        elif hasattr(link, "model_dump"):
            raw = dict(link.model_dump())
        else:
            raw = {
                "url": getattr(link, "url", ""),
                "text": getattr(link, "text", ""),
                "domain": getattr(link, "domain", None),
                "internal": getattr(link, "internal", False),
            }
        serialized.append(
            {
                "url": preview_text(str(raw.get("url", "")), limit=1000),
                "text": preview_text(str(raw.get("text", "")), limit=1000),
                "domain": preview_text(str(raw.get("domain", "")), limit=500)
                if raw.get("domain") is not None
                else None,
                "internal": bool(raw.get("internal", False)),
            }
        )
    return serialized


def serialize_tool_metadata(metadata: Any) -> dict[str, Any] | None:
    if not metadata:
        return None
    if hasattr(metadata, "model_dump"):
        metadata = metadata.model_dump()
    if not isinstance(metadata, dict):
        return {"value": preview_text(str(metadata), limit=_max_text_chars())}
    preferred_keys = (
        "title",
        "description",
        "site_name",
        "canonical_url",
        "domain",
        "language",
        "fetched_url",
    )
    keys = [key for key in preferred_keys if key in metadata]
    if not keys:
        keys = list(metadata.keys())[: _max_items()]
    return {str(key): _normalize_for_body(metadata[key]) for key in keys if key in metadata}


def _tool_request_fingerprint(tool_name: str, fields: dict[str, Any]) -> str:
    payload = {
        "tool_name": tool_name,
        "fields": _normalize_for_body(fields),
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return sha256(raw).hexdigest()[:16]


def serialize_tool_event_fields(
    phase: str,
    fields: dict[str, Any],
    *,
    tool_name: str | None = None,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for name, value in fields.items():
        lowered_name = name.lower()
        if any(
            marker in lowered_name
            for marker in (
                "authorization",
                "api_key",
                "apikey",
                "access_token",
                "password",
                "secret",
            )
        ):
            continue
        if name in {"page_content", "content_preview", "answer"}:
            normalized[name] = preview_text(str(value), limit=_max_text_chars())
        elif name == "results" and isinstance(value, list):
            normalized[name] = serialize_search_results(value)
        elif name == "links":
            normalized[name] = serialize_tool_links(value)
        elif name == "metadata":
            normalized[name] = serialize_tool_metadata(value)
        elif name == "error" and isinstance(value, dict):
            normalized[name] = {
                key: _normalize_for_body(val) for key, val in list(value.items())[: _max_items()]
            }
        else:
            normalized[name] = _normalize_for_body(value)

    if phase == "request":
        normalized["request_fingerprint"] = _tool_request_fingerprint(
            tool_name or str(fields.get("tool_name") or fields.get("name") or "tool"),
            fields,
        )

    return normalized


__all__ = [
    "current_trace_context",
    "get_current_run_key",
    "get_current_tool_call_id",
    "preview_text",
    "serialize_search_results",
    "serialize_tool_event_fields",
    "serialize_tool_links",
    "serialize_tool_metadata",
    "set_current_run_key",
    "set_current_tool_call_id",
]
