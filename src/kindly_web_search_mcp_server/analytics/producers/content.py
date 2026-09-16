"""Persistence helper for fetch / content tool outcomes.

Moved from ``utils/observability.py`` during the analytics/utils cutover.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

__all__ = ["_persist_content_analytics"]


def _persist_content_analytics(
    *,
    terminal_event_id: str,
    tool_call_id: str,
    tool_name: str,
    fields: dict[str, Any],
    payload: dict[str, Any],
    trace_context: dict[str, str],
    status: str,
    error_message: str | None,
    payload_json: dict[str, Any],
    logger: logging.Logger,
) -> None:
    try:
        from ..writers import insert_content_operation_batches
        from .events import _tool_input_count, _tool_output_count

        op_row = {
            "terminal_event_id": terminal_event_id,
            "tool_call_id": tool_call_id,
            "trace_id": trace_context.get("trace_id"),
            "session_id": fields.get("session_id"),
            "tool_name": tool_name,
            "input_count": _tool_input_count(fields),
            "output_count": _tool_output_count(fields),
            "duration_ms": fields.get("duration_ms"),
            "status": status,
            "error_type": fields.get("error_type"),
            "error_message": error_message,
            "payload_json": payload_json,
        }

        fetch_rows = []
        summary_rows = []

        stage_attempt_rows = []
        fetch_item_rows = []
        summary_rung_rows = []

        if tool_name == "fetch":
            items_data = fields.get("results") or payload.get("results") or []
            if not isinstance(items_data, list):
                items_data = []
            is_batch = fields.get("mode") == "bulk" or len(items_data) != 1

            for idx, item in enumerate(items_data):
                if not isinstance(item, dict):
                    continue
                raw_window = item.get("window")
                window: dict[str, Any] = raw_window if isinstance(raw_window, dict) else {}
                page_content = item.get("page_content") or ""
                fetch_rows.append(
                    {
                        "terminal_event_id": terminal_event_id,
                        "tool_call_id": tool_call_id,
                        "item_index": idx,
                        "input_url": item.get("input_url") or item.get("url"),
                        "normalized_url": item.get("normalized_url"),
                        "fetched_url": item.get("fetched_url") or item.get("url"),
                        "source_type": item.get("source_type"),
                        "content_type": item.get("content_type"),
                        "cached": item.get("cached"),
                        "fetch_backend": item.get("fetch_backend") or item.get("origin_backend"),
                        "status": item.get("status") or status,
                        "content_length": len(page_content)
                        if isinstance(page_content, str)
                        else None,
                        "page_char_count": item.get("page_char_count") or len(page_content),
                        "word_count": item.get("word_count") or len(str(page_content).split()),
                        "window_offset": window.get("offset"),
                        "window_length": window.get("length"),
                        "window_returned_chars": window.get("returned_chars"),
                        "window_total_chars": window.get("total_chars"),
                        "window_has_more": window.get("has_more"),
                        "window_next_offset": window.get("next_offset"),
                        "item_duration_ms": item.get("duration_ms") or fields.get("duration_ms"),
                        "payload_json": item,
                    }
                )
                item_stage_attempts = [
                    attempt
                    for attempt in (fields.get("stage_attempts") or [])
                    if isinstance(attempt, dict) and attempt.get("item_index", 0) == idx
                ]
                stage_count = len(item_stage_attempts)
                diagnostics = item.get("diagnostics") or []
                raw_error = item.get("error")
                raw_error = raw_error if isinstance(raw_error, dict) else {}
                stage_path_parts = [
                    str(attempt.get("stage"))
                    for attempt in item_stage_attempts
                    if attempt.get("stage")
                ]
                fetch_item_rows.append(
                    {
                        "terminal_event_id": terminal_event_id,
                        "tool_call_id": tool_call_id,
                        "item_index": idx,
                        "error_code": raw_error.get("code"),
                        "error_category": raw_error.get("category"),
                        "error_retryable": raw_error.get("retryable"),
                        "error_http_status": raw_error.get("http_status"),
                        "error_message": raw_error.get("message"),
                        "quality_score": (item.get("quality") or {}).get("score"),
                        "title": item.get("title"),
                        "bytes_downloaded": item.get("bytes_downloaded"),
                        "redirect_count": item.get("redirect_count"),
                        "stage_count": stage_count,
                        "stage_path": " > ".join(stage_path_parts) or None,
                        "diagnostics_json": diagnostics
                        if isinstance(diagnostics, (dict, list))
                        else None,
                    }
                )
                summary_data = item.get("summary")
                if isinstance(summary_data, dict):
                    summary_rows.append(
                        {
                            "terminal_event_id": terminal_event_id,
                            "tool_call_id": tool_call_id,
                            "item_index": idx,
                            "normalized_url": item.get("normalized_url"),
                            "focus_query": fields.get("focus_query"),
                            "input_chars": item.get("page_char_count")
                            or (len(page_content) if isinstance(page_content, str) else None),
                            "source_url_count": 1,
                            "is_batch": is_batch,
                            "batch_size": len(items_data),
                            "is_stub": bool(
                                summary_data.get("is_stub") or not summary_data.get("summary")
                            ),
                            "backend": summary_data.get("backend"),
                            "model_requested": summary_data.get("model_requested"),
                            "model_used": summary_data.get("model_used")
                            or summary_data.get("model"),
                            "fallback_attempted": summary_data.get("fallback_attempted"),
                            "fallback_tier": summary_data.get("fallback_tier"),
                            "input_tokens": summary_data.get("input_tokens"),
                            "output_tokens": summary_data.get("output_tokens"),
                            "total_tokens": summary_data.get("total_tokens"),
                            "summary_length_chars": len(summary_data.get("summary") or ""),
                            "key_points_count": len(summary_data.get("key_points", []))
                            if isinstance(summary_data.get("key_points"), list)
                            else 0,
                            "important_entities_count": len(
                                summary_data.get("important_entities", [])
                            )
                            if isinstance(summary_data.get("important_entities"), list)
                            else 0,
                            "verbatim_terms_count": len(summary_data.get("verbatim_terms", []))
                            if isinstance(summary_data.get("verbatim_terms"), list)
                            else 0,
                            "limitations_count": len(summary_data.get("limitations", []))
                            if isinstance(summary_data.get("limitations"), list)
                            else 0,
                            "source_date": summary_data.get("source_date"),
                            "status": status,
                            "error_type": fields.get("error_type"),
                            "error_message": error_message,
                            "duration_ms": item.get("duration_ms"),
                            "payload_json": summary_data,
                        }
                    )

        for rung_order, rung in enumerate(fields.get("summary_rungs") or [], start=1):
            if not isinstance(rung, dict):
                continue
            summary_rung_rows.append(
                {
                    "terminal_event_id": terminal_event_id,
                    "tool_call_id": tool_call_id,
                    "item_index": rung.get("item_index", 0),
                    "rung": rung.get("rung"),
                    "rung_order": rung_order,
                    "provider": rung.get("provider"),
                    "model_used": rung.get("model_used"),
                    "outcome": rung.get("outcome"),
                    "error_type": rung.get("error_type"),
                    "input_tokens": rung.get("input_tokens"),
                    "output_tokens": rung.get("output_tokens"),
                    "latency_ms": rung.get("latency_ms"),
                }
            )

        for attempt_order, attempt in enumerate(fields.get("stage_attempts") or [], start=1):
            if not isinstance(attempt, dict):
                continue
            stage_attempt_rows.append(
                {
                    "attempt_id": str(uuid4()),
                    "terminal_event_id": terminal_event_id,
                    "tool_call_id": tool_call_id,
                    "item_index": attempt.get("item_index", 0),
                    "normalized_url": attempt.get("normalized_url"),
                    "stage": attempt.get("stage"),
                    "stage_order": attempt.get("stage_order", attempt_order),
                    "outcome": attempt.get("outcome"),
                    "error_code": attempt.get("error_code"),
                    "error_category": attempt.get("error_category"),
                    "retryable": attempt.get("retryable"),
                    "http_status": attempt.get("http_status"),
                    "latency_ms": attempt.get("latency_ms"),
                    "attempt_count": attempt.get("attempt_count", 1),
                    "bytes_downloaded": attempt.get("bytes_downloaded"),
                    "chars_kept": attempt.get("chars_kept"),
                    "quality_score": attempt.get("quality_score"),
                    "skipped_reason": attempt.get("skipped_reason"),
                }
            )

        insert_content_operation_batches(
            content_operations=[op_row],
            content_fetches=fetch_rows,
            content_summaries=summary_rows,
            stage_attempts=stage_attempt_rows,
            fetch_items=fetch_item_rows,
            summary_rungs=summary_rung_rows,
        )
    except Exception as exc:
        logger.warning("Failed to persist content analytics: %s", exc)
