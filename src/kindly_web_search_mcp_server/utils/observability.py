from __future__ import annotations

import contextvars
import json
import logging
from hashlib import sha256
from typing import Any
from uuid import uuid4

from ..observability.events import PERSISTED_EVENT_PREFIXES
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


try:
    from opentelemetry import trace
except Exception:  # pragma: no cover - optional observability dependency
    trace = None  # type: ignore[assignment]


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
    if not context or not context.is_valid:
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
            provider_count = result.get("provider_count")
            score = result.get("score")
        else:
            title = getattr(result, "title", "") or ""
            link = getattr(result, "link", "") or ""
            snippet = getattr(result, "snippet", "") or ""
            domain = getattr(result, "domain", "")
            providers = list(getattr(result, "providers", []) or [])
            provider_count = getattr(result, "provider_count", None)
            score = getattr(result, "score", None)
        serialized.append(
            {
                "title": preview_text(title, limit=1000),
                "link": link,
                "snippet": preview_text(snippet, limit=4000),
                "domain": domain,
                "providers": providers,
                "provider_count": provider_count,
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


def _tool_status(phase: str, fields: dict[str, Any]) -> str:
    explicit = fields.get("status")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    if phase == "request":
        return "started"
    if phase == "error" or fields.get("isError") is True or fields.get("error"):
        return "error"
    output_count = _tool_output_count(fields)
    if output_count == 0:
        return "empty"
    return "success"


def _tool_int(fields: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = fields.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _tool_input_count(fields: dict[str, Any]) -> int | None:
    value = _tool_int(fields, "input_count", "query_count", "num_queries")
    if value is not None:
        return value
    for name in ("queries", "search_queries"):
        items = fields.get(name)
        if isinstance(items, (list, tuple)):
            return len(items)
    return None


def _tool_output_count(fields: dict[str, Any]) -> int | None:
    value = _tool_int(
        fields,
        "output_count",
        "result_count",
        "total_results",
        "total_citations",
        "returned_links",
        "num_results_returned",
    )
    if value is not None:
        return value
    for name in ("results", "citations", "links"):
        items = fields.get(name)
        if isinstance(items, (list, tuple)):
            return len(items)
    return None


def _insert_tool_call_analytics(
    *,
    tool_name: str,
    phase: str,
    tool_call_id: str,
    fields: dict[str, Any],
    payload: dict[str, Any],
    trace_context: dict[str, str],
    logger: logging.Logger,
) -> None:
    try:
        from ..settings import settings
        from ..analytics.duckdb_store import insert_tool_call_event
    except Exception as exc:  # pragma: no cover - optional analytics dependency
        logger.debug("DuckDB tool-call sink unavailable: %s", exc)
        return
    if not settings.analytics_enabled:
        return

    input_url = fields.get("url") or fields.get("video_id_or_url")
    normalized_url = fields.get("canonical_url") or fields.get("video_url")
    query = fields.get("query") or fields.get("objective")
    payload_with_context = dict(payload)
    payload_with_context["tool_call_id"] = tool_call_id
    try:
        insert_tool_call_event(
            event_id=str(uuid4()),
            tool_call_id=tool_call_id,
            session_id=fields.get("session_id"),
            trace_id=trace_context.get("trace_id"),
            span_id=trace_context.get("span_id"),
            tool_name=tool_name,
            phase=phase,
            status=_tool_status(phase, fields),
            query=str(query) if query is not None else None,
            research_goal=fields.get("research_goal") or fields.get("objective"),
            input_url=str(input_url) if input_url is not None else None,
            normalized_url=str(normalized_url) if normalized_url is not None else None,
            input_count=_tool_input_count(fields),
            output_count=_tool_output_count(fields),
            duration_ms=fields.get("duration_ms"),
            provider=fields.get("provider") or fields.get("provider_name"),
            model=fields.get("model") or fields.get("model_used") or fields.get("client_model"),
            input_tokens=fields.get("input_tokens"),
            output_tokens=fields.get("output_tokens"),
            request_fingerprint=payload.get("request_fingerprint"),
            error_type=fields.get("error_type"),
            error_message=(
                fields.get("error_message")
                if isinstance(fields.get("error_message"), str)
                else preview_text(str(fields.get("error")))
                if fields.get("error") is not None
                else None
            ),
            payload_json=payload_with_context,
        )
    except Exception as exc:  # pragma: no cover - best-effort sink
        logger.debug("DuckDB tool-call sink failed for %s: %s", tool_name, exc)


def _persist_analytics_event(
    event: str,
    payload: dict[str, Any],
    logger: logging.Logger,
) -> None:
    """Best-effort structured-log side channel only.

    Typed analytics persistence lives in table-specific writers
    (``insert_tool_call_event``, ``insert_provider_calls``, search outcome
    batches, etc.). The legacy ``search_events`` / ``append_event`` sink was
    removed; non-tool observability events remain log-only by design.
    """
    del payload  # reserved for a future typed sink; keep signature stable
    if not event.startswith(PERSISTED_EVENT_PREFIXES) or event.startswith("tool."):
        return
    logger.debug(
        "observability event %s is log-only; typed analytics uses insert_* writers",
        event,
    )


def emit_tool_observability_event(
    logger: logging.Logger,
    tool_name: str,
    phase: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    event = f"tool.{tool_name}.{phase}"
    trace_context = current_trace_context()
    explicit_tool_call_id = fields.get("tool_call_id")
    tool_call_id = (
        explicit_tool_call_id.strip()
        if isinstance(explicit_tool_call_id, str) and explicit_tool_call_id.strip()
        else get_current_tool_call_id()
    ) or str(uuid4())
    set_current_tool_call_id(tool_call_id)
    fields = dict(fields)
    fields["tool_call_id"] = tool_call_id
    payload = {"event": event, "tool_name": tool_name}
    payload.update(trace_context)
    payload.update(serialize_tool_event_fields(phase, fields, tool_name=tool_name))

    extra: dict[str, str | bool | int | float | None] = {"obs_event": event}
    for name, value in payload.items():
        if name == "event":
            continue
        extra[_record_key(name)] = _normalize_for_extra(value)

    logger.log(level, json.dumps(payload, ensure_ascii=True, sort_keys=True), extra=extra)
    _insert_tool_call_analytics(
        tool_name=tool_name,
        phase=phase,
        tool_call_id=tool_call_id,
        fields=fields,
        payload=payload,
        trace_context=trace_context,
        logger=logger,
    )


def emit_observability_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    trace_context = current_trace_context()
    payload = {"event": event}
    payload.update(trace_context)
    payload.update({name: _normalize_for_body(value) for name, value in fields.items()})

    # Auto-inject run_key from context if not explicitly provided
    if "run_key" not in payload:
        ctx_run_key = get_current_run_key()
        if ctx_run_key is not None:
            payload["run_key"] = ctx_run_key

    analytics_payload = {"event": event}
    analytics_payload.update(trace_context)
    analytics_payload.update(
        {name: _normalize_for_analytics(value) for name, value in fields.items()}
    )
    # Auto-inject run_key into analytics payload as well
    if "run_key" not in analytics_payload:
        ctx_run_key = get_current_run_key()
        if ctx_run_key is not None:
            analytics_payload["run_key"] = ctx_run_key

    extra: dict[str, str | bool | int | float | None] = {"obs_event": event}
    for name, value in payload.items():
        if name == "event":
            continue
        extra[_record_key(name)] = _normalize_for_extra(value)

    logger.log(
        level,
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        extra=extra,
    )
    _persist_analytics_event(event, analytics_payload, logger)
