"""Persistence helpers for quick_web_search and gemini_search tool outcomes.

Moved from ``utils/observability.py`` during the analytics/utils cutover.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = [
    "_persist_quick_web_search_analytics",
    "_persist_gemini_search_analytics",
]


def _persist_quick_web_search_analytics(
    *,
    terminal_event_id: str,
    tool_call_id: str,
    fields: dict[str, Any],
    payload: dict[str, Any],
    trace_context: dict[str, str],
    status: str,
    error_message: str | None,
    payload_json: dict[str, Any],
    logger: logging.Logger,
) -> None:
    try:
        from ..writers import insert_quick_web_search_batches

        citations_data = fields.get("citations") or payload.get("citations") or []
        citation_rows = []
        if isinstance(citations_data, list):
            for idx, cit in enumerate(citations_data):
                if isinstance(cit, dict):
                    citation_rows.append(
                        {
                            "terminal_event_id": terminal_event_id,
                            "tool_call_id": tool_call_id,
                            "citation_index": idx,
                            "title": cit.get("title"),
                            "url": cit.get("url"),
                            "snippet": cit.get("snippet"),
                            "publish_date": cit.get("publish_date"),
                            "excerpts": cit.get("excerpts"),
                            "payload_json": cit,
                        }
                    )
                elif hasattr(cit, "model_dump"):
                    cd = cit.model_dump()
                    citation_rows.append(
                        {
                            "terminal_event_id": terminal_event_id,
                            "tool_call_id": tool_call_id,
                            "citation_index": idx,
                            "title": getattr(cit, "title", None),
                            "url": getattr(cit, "url", None),
                            "snippet": getattr(cit, "snippet", None),
                            "publish_date": getattr(cit, "publish_date", None),
                            "excerpts": getattr(cit, "excerpts", None),
                            "payload_json": cd,
                        }
                    )

        run_row = {
            "terminal_event_id": terminal_event_id,
            "tool_call_id": tool_call_id,
            "trace_id": trace_context.get("trace_id"),
            "session_id": fields.get("session_id"),
            "search_id": fields.get("search_id"),
            "provider_session_id": fields.get("provider_session_id") or fields.get("session_id"),
            "search_queries": fields.get("search_queries"),
            "objective": fields.get("objective"),
            "max_results": fields.get("max_results"),
            "max_chars_total": fields.get("max_chars_total"),
            "max_chars_per_result": fields.get("max_chars_per_result"),
            "client_model": fields.get("client_model"),
            "include_domains": fields.get("include_domains"),
            "exclude_domains": fields.get("exclude_domains"),
            "after_date": fields.get("after_date"),
            "location": fields.get("location"),
            "max_age_seconds": fields.get("max_age_seconds"),
            "timeout_seconds": fields.get("timeout_seconds"),
            "disable_cache_fallback": fields.get("disable_cache_fallback"),
            "status": status,
            "duration_ms": fields.get("duration_ms"),
            "total_citations": fields.get("total_citations") or len(citation_rows),
            "warnings": fields.get("warnings"),
            "usage": fields.get("usage"),
            "error_type": fields.get("error_type"),
            "error_message": error_message,
            "payload_json": payload_json,
        }

        insert_quick_web_search_batches(
            quick_web_search_runs=[run_row],
            quick_web_search_citations=citation_rows,
        )
    except Exception as exc:
        logger.debug("Failed to persist quick_web_search analytics: %s", exc)


def _persist_gemini_search_analytics(
    *,
    terminal_event_id: str,
    tool_call_id: str,
    fields: dict[str, Any],
    payload: dict[str, Any],
    trace_context: dict[str, str],
    status: str,
    error_message: str | None,
    payload_json: dict[str, Any],
    logger: logging.Logger,
) -> None:
    try:
        from ..writers import insert_gemini_search_batches

        sources_data = fields.get("sources") or payload.get("sources") or []
        url_citations_data = fields.get("url_citations") or payload.get("url_citations") or []
        source_rows = []
        if isinstance(sources_data, list):
            for idx, src in enumerate(sources_data):
                if isinstance(src, dict):
                    source_rows.append(
                        {
                            "terminal_event_id": terminal_event_id,
                            "tool_call_id": tool_call_id,
                            "source_kind": "grounding_source",
                            "source_index": idx,
                            "url": src.get("url"),
                            "title": src.get("title"),
                            "source_json": src,
                        }
                    )
                elif hasattr(src, "model_dump"):
                    source_rows.append(
                        {
                            "terminal_event_id": terminal_event_id,
                            "tool_call_id": tool_call_id,
                            "source_kind": "grounding_source",
                            "source_index": idx,
                            "url": getattr(src, "url", None),
                            "title": getattr(src, "title", None),
                            "source_json": src.model_dump(),
                        }
                    )

        if isinstance(url_citations_data, list):
            for idx, src in enumerate(url_citations_data):
                if isinstance(src, dict):
                    source_rows.append(
                        {
                            "terminal_event_id": terminal_event_id,
                            "tool_call_id": tool_call_id,
                            "source_kind": "url_citation",
                            "source_index": idx,
                            "url": src.get("url"),
                            "title": src.get("title"),
                            "source_json": src,
                        }
                    )
                elif hasattr(src, "model_dump"):
                    source_rows.append(
                        {
                            "terminal_event_id": terminal_event_id,
                            "tool_call_id": tool_call_id,
                            "source_kind": "url_citation",
                            "source_index": idx,
                            "url": getattr(src, "url", None),
                            "title": getattr(src, "title", None),
                            "source_json": src.model_dump(),
                        }
                    )

        run_row = {
            "terminal_event_id": terminal_event_id,
            "tool_call_id": tool_call_id,
            "trace_id": trace_context.get("trace_id"),
            "session_id": fields.get("session_id"),
            "query": fields.get("query") or "",
            "research_goal": fields.get("research_goal"),
            "structured_output_requested": bool(
                fields.get("structured_output") or fields.get("structured_output_requested")
            ),
            "mode": fields.get("mode"),
            "answer": fields.get("answer"),
            "structured_data": fields.get("structured_data"),
            "search_queries": fields.get("search_queries"),
            "model_used": fields.get("model_used") or fields.get("model"),
            "prompt_tokens": fields.get("prompt_tokens"),
            "completion_tokens": fields.get("completion_tokens"),
            "total_tokens": fields.get("total_tokens"),
            "grounding_chunks_count": (
                fields.get("grounding_chunks_count") or fields.get("grounding_chunks")
            ),
            "web_search_queries_count": (
                fields.get("web_search_queries_count") or fields.get("grounding_queries")
            ),
            "fallback_chain": fields.get("fallback_chain"),
            "fallback_reason": fields.get("fallback_reason"),
            "status": status,
            "duration_ms": fields.get("duration_ms"),
            "error_message": error_message,
            "payload_json": payload_json,
        }

        insert_gemini_search_batches(
            gemini_search_runs=[run_row],
            gemini_search_sources=source_rows,
        )
    except Exception as exc:
        logger.debug("Failed to persist gemini_search analytics: %s", exc)
