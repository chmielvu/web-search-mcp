"""Observability event emitters and the per-tool call analytics sink.

Moved from ``utils/observability.py`` during the analytics/utils cutover.

Public emitters:
- :func:`emit_observability_event` — structured log event with run-key linkage
- :func:`emit_tool_observability_event` — bounded MCP tool lifecycle event

The ``PERSISTED_EVENT_PREFIXES`` constant lives in :mod:`analytics.events`; this
module imports it at module load (no longer needs the lazy ``import`` workaround
that ``utils/observability.py`` used to dodge the analytics↔utils cycle — the
cycle is broken by the move itself).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from ...settings import settings
from ...utils.observability import (
    _normalize_for_body,
    _normalize_for_extra,
    _normalize_for_analytics,
    _record_key,
    current_trace_context,
    get_current_run_key,
    get_current_tool_call_id,
    preview_text,
    serialize_tool_event_fields,
    set_current_tool_call_id,
)

from ..events import PERSISTED_EVENT_PREFIXES

from ..ids import _canonical_result_id as _cri


__all__ = [
    "_insert_tool_call_analytics",
    "_persist_analytics_event",
    "_persist_tool_output_items",
    "_tool_input_count",
    "_tool_int",
    "_tool_output_count",
    "_tool_status",
    "emit_observability_event",
    "emit_tool_observability_event",
]


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


def _resolve_session_id() -> str | None:
    """Best-effort per-session identifier for typed analytics rows.

    Prefers an explicit ``session_id`` field, then the FastMCP request
    context (works for HTTP headers and generates a stable per-session
    UUID for stdio). Returns None outside a request context (CLI, tests)
    so rows stay honest instead of carrying a fabricated id.
    """
    try:
        from fastmcp.server.dependencies import get_context

        ctx = get_context()
        if ctx is not None:
            try:
                session_id = ctx.session_id
                if session_id:
                    return str(session_id)
            except Exception:
                pass
            try:
                client_id = ctx.client_id
                if client_id:
                    return str(client_id)
            except Exception:
                pass
    except Exception:  # noqa: BLE001  # outside a request context
        pass
    return None


def _persist_tool_output_items(
    *,
    tool_name: str,
    tool_call_id: str,
    fields: dict[str, Any],
    logger: logging.Logger,
) -> None:
    """Persist output items from tool responses for cross-tool linkage."""
    try:
        rows: list[dict[str, Any]] = []
        run_key = get_current_run_key()
        session_id = fields.get("session_id")

        items: list[tuple[int, dict[str, Any]]] = []
        if tool_name == "web_search":
            results = fields.get("results") or []
            for rank, item in enumerate(results, start=1):
                if isinstance(item, dict):
                    link = item.get("link") or item.get("url") or ""
                    title = item.get("title") or ""
                    snippet = item.get("snippet") or ""
                else:
                    link = getattr(item, "link", "") or getattr(item, "url", "") or ""
                    title = getattr(item, "title", "") or ""
                    snippet = getattr(item, "snippet", "") or ""
                rows.append(
                    {
                        "output_item_id": _cri(f"{tool_call_id}|result|{rank}"),
                        "tool_call_id": tool_call_id,
                        "session_id": session_id,
                        "run_key": run_key,
                        "tool_name": tool_name,
                        "item_type": "result",
                        "item_rank": rank,
                        "canonical_result_id": _cri(link) if link else None,
                        "raw_url": link,
                        "title": title,
                        "snippet": snippet,
                    }
                )
        elif tool_name == "fetch":
            raw_items = fields.get("results") or []
            items = [
                (rank, item)
                for rank, item in enumerate(raw_items, start=1)
                if isinstance(item, dict)
            ]
        for rank, item in items:
            url = item.get("url") or item.get("input_url") if isinstance(item, dict) else ""
            if url:
                rows.append(
                    {
                        "output_item_id": _cri(f"{tool_call_id}|content|{rank}"),
                        "tool_call_id": tool_call_id,
                        "session_id": session_id,
                        "run_key": run_key,
                        "tool_name": tool_name,
                        "item_type": "content",
                        "item_rank": rank,
                        "canonical_result_id": _cri(url),
                        "raw_url": url,
                        "title": item.get("title") if isinstance(item, dict) else None,
                        "snippet": None,
                    }
                )
        if tool_name == "gemini_search":
            sources = fields.get("sources") or []
            for rank, src in enumerate(sources, start=1):
                if isinstance(src, dict):
                    url = src.get("url") or ""
                    title = src.get("title") or ""
                else:
                    url = getattr(src, "url", "") or ""
                    title = getattr(src, "title", "") or ""
                rows.append(
                    {
                        "output_item_id": _cri(f"{tool_call_id}|source|{rank}"),
                        "tool_call_id": tool_call_id,
                        "session_id": session_id,
                        "run_key": run_key,
                        "tool_name": tool_name,
                        "item_type": "source",
                        "item_rank": rank,
                        "canonical_result_id": _cri(url) if url else None,
                        "raw_url": url,
                        "title": title,
                        "snippet": None,
                    }
                )
        if rows:
            from ..writers import insert_funnel_uplift_batches

            insert_funnel_uplift_batches(tool_output_items=rows)
    except Exception as exc:
        logger.debug("Failed to persist tool output items: %s", exc)


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
    if not settings.analytics_enabled:
        return

    input_url = fields.get("url") or fields.get("video_id_or_url")
    normalized_url = fields.get("canonical_url") or fields.get("video_url")
    query = fields.get("query") or fields.get("objective")
    payload_with_context = dict(payload)
    payload_with_context["tool_call_id"] = tool_call_id
    event_id = str(uuid4())
    status = _tool_status(phase, fields)
    error_message = (
        fields.get("error_message")
        if isinstance(fields.get("error_message"), str)
        else preview_text(str(fields.get("error")))
        if fields.get("error") is not None
        else None
    )
    try:
        from ..writers import insert_tool_call_event

        insert_tool_call_event(
            event_id=event_id,
            tool_call_id=tool_call_id,
            run_key=get_current_run_key(),
            session_id=fields.get("session_id") or _resolve_session_id(),
            trace_id=trace_context.get("trace_id"),
            span_id=trace_context.get("span_id"),
            tool_name=tool_name,
            phase=phase,
            status=status,
            query=str(query) if query is not None else None,
            research_goal=fields.get("research_goal") or fields.get("objective"),
            input_url=str(input_url) if input_url is not None else None,
            normalized_url=str(normalized_url) if normalized_url is not None else None,
            input_count=_tool_input_count(fields),
            output_count=_tool_output_count(fields),
            duration_ms=fields.get("duration_ms"),
            provider=fields.get("provider") or fields.get("provider_name"),
            output_tokens=fields.get("output_tokens"),
            request_fingerprint=payload.get("request_fingerprint"),
            error_type=fields.get("error_type"),
            error_message=error_message,
            payload_json=payload_with_context,
        )
    except Exception as exc:  # pragma: no cover - best-effort sink
        logger.debug("DuckDB tool-call sink failed for %s: %s", tool_name, exc)

    if phase in ("response", "error"):
        from .code_search import _persist_code_search_analytics
        from .content import _persist_content_analytics
        from .quick_search import (
            _persist_gemini_search_analytics,
            _persist_quick_web_search_analytics,
        )

        if tool_name == "quick_web_search":
            _persist_quick_web_search_analytics(
                tool_call_id=tool_call_id,
                fields=fields,
                payload=payload,
                trace_context=trace_context,
                status=status,
                error_message=error_message,
                payload_json=payload_with_context,
                logger=logger,
            )
        elif tool_name == "gemini_search":
            _persist_gemini_search_analytics(
                terminal_event_id=event_id,
                tool_call_id=tool_call_id,
                fields=fields,
                payload=payload,
                trace_context=trace_context,
                status=status,
                error_message=error_message,
                payload_json=payload_with_context,
                logger=logger,
            )
        elif tool_name == "fetch":
            _persist_content_analytics(
                terminal_event_id=event_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                fields=fields,
                payload=payload,
                trace_context=trace_context,
                status=status,
                error_message=error_message,
                payload_json=payload_with_context,
                logger=logger,
            )
        elif tool_name == "code_search":
            _persist_code_search_analytics(
                terminal_event_id=event_id,
                tool_call_id=tool_call_id,
                fields=fields,
                payload=payload,
                trace_context=trace_context,
                status=status,
                error_message=error_message,
                payload_json=payload_with_context,
                logger=logger,
            )
        # Persist tool output items for cross-tool linkage
        if tool_name in ("web_search", "fetch", "gemini_search"):
            _persist_tool_output_items(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                fields=fields,
                logger=logger,
            )


def _persist_analytics_event(
    event: str,
    payload: dict[str, Any],
    logger: logging.Logger,
) -> None:
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
