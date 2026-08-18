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
        from ..analytics.duckdb_store import insert_quick_web_search_batches

        citations_data = fields.get("citations") or payload.get("citations") or []
        citation_rows = []
        if isinstance(citations_data, list):
            for idx, cit in enumerate(citations_data):
                if isinstance(cit, dict):
                    citation_rows.append({
                        "terminal_event_id": terminal_event_id,
                        "tool_call_id": tool_call_id,
                        "citation_index": idx,
                        "title": cit.get("title"),
                        "url": cit.get("url"),
                        "snippet": cit.get("snippet"),
                        "publish_date": cit.get("publish_date"),
                        "excerpts": cit.get("excerpts"),
                        "payload_json": cit,
                    })
                elif hasattr(cit, "model_dump"):
                    cd = cit.model_dump()
                    citation_rows.append({
                        "terminal_event_id": terminal_event_id,
                        "tool_call_id": tool_call_id,
                        "citation_index": idx,
                        "title": getattr(cit, "title", None),
                        "url": getattr(cit, "url", None),
                        "snippet": getattr(cit, "snippet", None),
                        "publish_date": getattr(cit, "publish_date", None),
                        "excerpts": getattr(cit, "excerpts", None),
                        "payload_json": cd,
                    })

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
        from ..analytics.duckdb_store import insert_gemini_search_batches

        sources_data = fields.get("sources") or payload.get("sources") or []
        url_citations_data = fields.get("url_citations") or payload.get("url_citations") or []
        source_rows = []
        if isinstance(sources_data, list):
            for idx, src in enumerate(sources_data):
                if isinstance(src, dict):
                    source_rows.append({
                        "terminal_event_id": terminal_event_id,
                        "tool_call_id": tool_call_id,
                        "source_kind": "grounding_source",
                        "source_index": idx,
                        "url": src.get("url"),
                        "title": src.get("title"),
                        "source_json": src,
                    })
                elif hasattr(src, "model_dump"):
                    source_rows.append({
                        "terminal_event_id": terminal_event_id,
                        "tool_call_id": tool_call_id,
                        "source_kind": "grounding_source",
                        "source_index": idx,
                        "url": getattr(src, "url", None),
                        "title": getattr(src, "title", None),
                        "source_json": src.model_dump(),
                    })

        if isinstance(url_citations_data, list):
            for idx, src in enumerate(url_citations_data):
                if isinstance(src, dict):
                    source_rows.append({
                        "terminal_event_id": terminal_event_id,
                        "tool_call_id": tool_call_id,
                        "source_kind": "url_citation",
                        "source_index": idx,
                        "url": src.get("url"),
                        "title": src.get("title"),
                        "source_json": src,
                    })
                elif hasattr(src, "model_dump"):
                    source_rows.append({
                        "terminal_event_id": terminal_event_id,
                        "tool_call_id": tool_call_id,
                        "source_kind": "url_citation",
                        "source_index": idx,
                        "url": getattr(src, "url", None),
                        "title": getattr(src, "title", None),
                        "source_json": src.model_dump(),
                    })

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


def _persist_code_search_analytics(
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
        from ..analytics.duckdb_store import insert_code_search_batches

        request = fields.get("request")
        plan = fields.get("plan")
        response = fields.get("response")
        plan_meta = getattr(plan, "metadata", None)
        stats = getattr(response, "stats", None)
        query_val = fields.get("query") or (getattr(request, "query", "") if request else "")

        if not request or not response:
            run_row = {
                "terminal_event_id": terminal_event_id,
                "tool_call_id": tool_call_id,
                "trace_id": trace_context.get("trace_id"),
                "session_id": fields.get("session_id"),
                "query": str(query_val),
                "research_goal": fields.get("research_goal"),
                "language": fields.get("language"),
                "path": fields.get("path"),
                "filename": fields.get("filename"),
                "extension": fields.get("extension"),
                "regexp_requested": fields.get("regexp"),
                "deep_requested": fields.get("deep"),
                "max_results_requested": fields.get("max_results"),
                "repo_name": fields.get("repo_name"),
                "library_name": fields.get("library_name"),
                "topic": fields.get("topic"),
                "repository_filters": fields.get("repositories"),
                "planner_original_query": None,
                "planner_search_text": None,
                "planner_api_query": None,
                "planner_mode": fields.get("mode", "code"),
                "planner_structural_kind": None,
                "planner_exa_semantic_query": None,
                "planner_regex_source": None,
                "planner_anchor_terms": None,
                "planner_concept_terms": None,
                "planner_source_tokens": None,
                "planner_qualifiers": None,
                "planner_warnings": None,
                "planner_backend_channels": fields.get("channels"),
                "planner_variants": None,
                "planner_variant_kinds": None,
                "provider_response_count": None,
                "provider_hit_counts": fields.get("providers"),
                "request_count": None,
                "hydration_count": None,
                "rerank_count": None,
                "returned_count": fields.get("output_count"),
                "repository_count": None,
                "diagnostic_count": None,
                "truncated": None,
                "dropped_count": None,
                "estimated_output_tokens": None,
                "duration_ms": fields.get("duration_ms"),
                "outcome": fields.get("outcome") or status,
                "error_type": fields.get("error_type"),
                "error_message": error_message,
                "payload_json": payload_json,
            }
            insert_code_search_batches(code_search_runs=[run_row])
            return

        run_row = {
            "terminal_event_id": terminal_event_id,
            "tool_call_id": tool_call_id,
            "trace_id": trace_context.get("trace_id"),
            "session_id": fields.get("session_id"),
            "query": getattr(request, "query", str(query_val)),
            "research_goal": getattr(request, "research_goal", None),
            "language": getattr(request, "language", None),
            "path": getattr(request, "path", None),
            "filename": getattr(request, "filename", None),
            "extension": getattr(request, "extension", None),
            "regexp_requested": getattr(request, "regexp", False),
            "deep_requested": getattr(request, "deep", False),
            "max_results_requested": getattr(request, "max_results", None),
            "repo_name": getattr(request, "repo_name", None),
            "library_name": getattr(request, "library_name", None),
            "topic": getattr(request, "topic", None),
            "repository_filters": (
                list(request.repositories)
                if hasattr(request, "repositories") and request.repositories
                else None
            ),
            "planner_original_query": getattr(plan, "original_query", None),
            "planner_search_text": getattr(plan, "search_text", None),
            "planner_api_query": getattr(plan, "api_query", None),
            "planner_mode": getattr(plan, "mode", None),
            "planner_structural_kind": getattr(plan_meta, "structural_kind", None),
            "planner_exa_semantic_query": getattr(plan_meta, "exa_semantic_query", None),
            "planner_regex_source": getattr(plan_meta, "regex_source", None),
            "planner_anchor_terms": getattr(plan, "anchor_terms", None),
            "planner_concept_terms": getattr(plan_meta, "concept_terms", None),
            "planner_source_tokens": getattr(plan_meta, "source_tokens", None),
            "planner_qualifiers": getattr(plan_meta, "qualifiers", None),
            "planner_warnings": getattr(plan_meta, "warnings", None),
            "planner_backend_channels": getattr(plan_meta, "backend_channels", None),
            "planner_variants": getattr(plan, "variants", None),
            "planner_variant_kinds": getattr(plan_meta, "variant_kinds", None),
            "provider_response_count": (
                len(getattr(response, "providers", []) or [])
            ),
            "provider_hit_counts": getattr(stats, "provider_counts", None),
            "request_count": getattr(stats, "request_count", None),
            "hydration_count": getattr(stats, "hydration_count", None),
            "rerank_count": getattr(stats, "rerank_count", None),
            "returned_count": len(getattr(response, "results", []) or []),
            "repository_count": len(getattr(response, "repositories", []) or []),
            "diagnostic_count": len(getattr(response, "diagnostics", []) or []),
            "truncated": getattr(stats, "truncated", None),
            "dropped_count": getattr(stats, "dropped_count", None),
            "estimated_output_tokens": getattr(stats, "estimated_output_tokens", None),
            "duration_ms": fields.get("duration_ms"),
            "outcome": getattr(response, "outcome", status),
            "error_type": fields.get("error_type"),
            "error_message": error_message,
            "payload_json": payload_json,
        }

        provider_rows = []
        compiled_map = (
            getattr(plan_meta, "compiled_queries", {})
            if plan_meta
            else {}
        )
        for idx, p in enumerate(getattr(response, "providers", []) or []):
            provider_rows.append({
                "terminal_event_id": terminal_event_id,
                "response_index": idx,
                "provider": p.provider,
                "hit_count": len(p.hits) if hasattr(p, "hits") else 0,
                "request_count": getattr(p, "request_count", 0),
                "outcome": getattr(p, "outcome", "ok"),
                "compiled_queries": (
                    compiled_map.get(p.provider) if isinstance(compiled_map, dict) else None
                ),
                "duration_ms": None,
                "error_type": None,
                "error_message": None,
                "payload_json": getattr(p, "metadata", None),
            })

        diagnostic_rows = []
        for idx, d in enumerate(getattr(response, "diagnostics", []) or []):
            diagnostic_rows.append({
                "terminal_event_id": terminal_event_id,
                "diagnostic_index": idx,
                "provider": getattr(d, "provider", None),
                "outcome": getattr(d, "outcome", "error"),
                "failure_kind": getattr(d, "failure_kind", "provider"),
                "message": getattr(d, "message", None),
                "status_code": getattr(d, "status_code", None),
                "retry_after_seconds": getattr(d, "retry_after_seconds", None),
                "query": getattr(d, "query", None),
                "details": getattr(d, "details", None),
            })

        hit_rows = []
        hit_variant_rows = []
        for hit_rank, hit in enumerate(getattr(response, "results", []) or [], 1):
            location = getattr(hit, "location", None)
            hit_rows.append({
                "terminal_event_id": terminal_event_id,
                "hit_rank": hit_rank,
                "url": getattr(hit, "url", ""),
                "repository": getattr(hit, "repository", None),
                "path": getattr(hit, "path", None),
                "sha": getattr(hit, "sha", None),
                "provider": getattr(hit, "provider", "unknown"),
                "query_variant": getattr(hit, "query_variant", None),
                "search_rank": getattr(hit, "search_rank", None),
                "result_kind": getattr(hit, "result_kind", "code_match"),
                "evidence_role": getattr(hit, "evidence_role", None),
                "title": getattr(hit, "title", None),
                "snippet": getattr(hit, "snippet", None),
                "published_date": getattr(hit, "published_date", None),
                "final_score": getattr(hit, "score", None),
                "score_components": getattr(hit, "score_components", None),
                "reasons": getattr(hit, "reasons", None),
                "hydrated": bool(getattr(hit, "hydrated_source", None)),
                "hydrated_source_truncated": getattr(
                    hit, "hydrated_source_truncated", False
                ),
                "line_start": getattr(hit, "line_start", None),
                "line_end": getattr(hit, "line_end", None),
                "commit_oid": getattr(hit, "commit_oid", None),
                "fragment_count": len(getattr(hit, "fragments", []) or []),
                "symbol_count": len(getattr(hit, "symbols", []) or []),
                "match_span_count": len(getattr(hit, "match_spans", []) or []),
                "location_precision": getattr(location, "precision", "unknown"),
                "lines_available": getattr(location, "lines_available", False),
                "revision_available": getattr(location, "revision_available", False),
                "match_data_available": getattr(location, "match_data_available", False),
                "source_metadata": getattr(hit, "source_metadata", None),
                "payload_json": None,
            })
            if getattr(hit, "query_variant", None):
                hit_variant_rows.append({
                    "terminal_event_id": terminal_event_id,
                    "hit_rank": hit_rank,
                    "association_index": 0,
                    "variant_index": None,
                    "provider": getattr(hit, "provider", None),
                    "query_variant": getattr(hit, "query_variant", None),
                    "search_rank": getattr(hit, "search_rank", None),
                })

        query_variant_rows = []
        variants = getattr(plan, "variants", []) or []
        variant_kinds = getattr(plan_meta, "variant_kinds", []) or []
        for v_idx, q_text in enumerate(variants):
            q_kind = variant_kinds[v_idx] if v_idx < len(variant_kinds) else None
            query_variant_rows.append({
                "terminal_event_id": terminal_event_id,
                "variant_index": v_idx,
                "query_text": q_text,
                "variant_kind": q_kind,
            })

        repo_rows = []
        for r_idx, repo in enumerate(getattr(response, "repositories", []) or []):
            repo_rows.append({
                "terminal_event_id": terminal_event_id,
                "repository_index": r_idx,
                "name_with_owner": getattr(repo, "name_with_owner", None),
                "url": getattr(repo, "url", None),
                "description": getattr(repo, "description", None),
                "stars": getattr(repo, "stars", 0),
                "forks": getattr(repo, "forks", 0),
                "pushed_at": getattr(repo, "pushed_at", None),
                "language": getattr(repo, "language", None),
                "topics": getattr(repo, "topics", None),
                "license_spdx_id": getattr(repo, "license_spdx_id", None),
                "homepage_url": getattr(repo, "homepage_url", None),
                "default_branch": getattr(repo, "default_branch", None),
                "head_oid": getattr(repo, "head_oid", None),
                "archived": getattr(repo, "archived", False),
                "fork": getattr(repo, "fork", False),
                "discovery_rank": getattr(repo, "discovery_rank", None),
                "discovery_score": getattr(repo, "discovery_score", 0.0),
                "discovery_queries": getattr(repo, "discovery_queries", None),
                "proof_hits": getattr(repo, "proof_hits", 0),
                "proof_paths": getattr(repo, "proof_paths", None),
                "proof_providers": getattr(repo, "proof_providers", None),
                "verified": getattr(repo, "verified", False),
                "payload_json": None,
            })

        rerank_rows = []
        if stats and getattr(stats, "rerank_count", 0) > 0:
            rerank_rows.append({
                "terminal_event_id": terminal_event_id,
                "provider": "cloud_rerank",
                "model": "cloud_reranker",
                "input_count": getattr(stats, "hydration_count", None) or getattr(stats, "request_count", None),
                "output_count": len(getattr(response, "results", []) or []),
                "reranked_count": getattr(stats, "rerank_count", 0),
                "status": "success",
                "diagnostic_outcome": None,
                "diagnostic_message": None,
                "duration_ms": None,
                "payload_json": None,
            })
        insert_code_search_batches(
            code_search_runs=[run_row],
            code_search_providers=provider_rows,
            code_search_diagnostics=diagnostic_rows,
            code_search_hits=hit_rows,
            code_search_hit_variants=hit_variant_rows,
            code_search_query_variants=query_variant_rows,
            code_search_repositories=repo_rows,
            code_search_rerank=rerank_rows,
        )
    except Exception as exc:
        logger.debug("Failed to persist code_search analytics: %s", exc)

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
        from ..analytics.duckdb_store import insert_content_operation_batches

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

        if tool_name == "get_content":
            fetch_rows.append({
                "terminal_event_id": terminal_event_id,
                "tool_call_id": tool_call_id,
                "item_index": 0,
                "input_url": fields.get("url") or fields.get("input_url"),
                "normalized_url": fields.get("canonical_url") or fields.get("normalized_url"),
                "fetched_url": fields.get("fetched_url"),
                "source_type": fields.get("source_type"),
                "fetch_backend": fields.get("fetch_backend") or fields.get("origin_backend"),
                "status": fields.get("fetch_status") or status,
                "content_length": fields.get("content_length"),
                "page_char_count": fields.get("page_char_count"),
                "word_count": fields.get("word_count"),
                "window_offset": fields.get("char_offset") or fields.get("window_offset"),
                "window_length": fields.get("char_length") or fields.get("window_length"),
                "window_returned_chars": fields.get("window_returned_chars"),
                "window_total_chars": fields.get("window_total_chars"),
                "window_has_more": fields.get("window_has_more"),
                "window_next_offset": fields.get("window_next_offset"),
                "item_duration_ms": fields.get("duration_ms"),
                "payload_json": payload_json,
            })
            summary_data = fields.get("summary") or payload.get("summary")
            if isinstance(summary_data, dict):
                summary_rows.append({
                    "terminal_event_id": terminal_event_id,
                    "tool_call_id": tool_call_id,
                    "item_index": 0,
                    "normalized_url": fields.get("canonical_url") or fields.get("normalized_url"),
                    "focus_query": fields.get("focus_query"),
                    "input_chars": fields.get("input_chars") or (
                        len(str(fields.get("page_content") or ""))
                        if fields.get("page_content")
                        else None
                    ),
                    "source_url_count": 1,
                    "is_batch": False,
                    "batch_size": 1,
                    "is_stub": bool(summary_data.get("is_stub") or not summary_data.get("summary")),
                    "backend": summary_data.get("backend") or fields.get("summary_backend"),
                    "model_requested": summary_data.get("model_requested"),
                    "model_used": summary_data.get("model_used") or summary_data.get("model"),
                    "fallback_attempted": summary_data.get("fallback_attempted"),
                    "fallback_tier": summary_data.get("fallback_tier"),
                    "input_tokens": summary_data.get("input_tokens"),
                    "output_tokens": summary_data.get("output_tokens"),
                    "total_tokens": summary_data.get("total_tokens"),
                    "summary_length_chars": (
                        len(summary_data.get("summary") or "")
                        if summary_data.get("summary")
                        else 0
                    ),
                    "key_points_count": (
                        len(summary_data.get("key_points", []))
                        if isinstance(summary_data.get("key_points"), list)
                        else 0
                    ),
                    "important_entities_count": (
                        len(summary_data.get("important_entities", []))
                        if isinstance(summary_data.get("important_entities"), list)
                        else 0
                    ),
                    "verbatim_terms_count": (
                        len(summary_data.get("verbatim_terms", []))
                        if isinstance(summary_data.get("verbatim_terms"), list)
                        else 0
                    ),
                    "limitations_count": (
                        len(summary_data.get("limitations", []))
                        if isinstance(summary_data.get("limitations"), list)
                        else 0
                    ),
                    "source_date": summary_data.get("source_date"),
                    "status": status,
                    "error_type": fields.get("error_type"),
                    "error_message": error_message,
                    "duration_ms": fields.get("duration_ms"),
                    "payload_json": summary_data,
                })
        elif tool_name == "batch_get_content":
            items_data = fields.get("items") or fields.get("results") or payload.get("items") or []
            if isinstance(items_data, list):
                for idx, item in enumerate(items_data):
                    if isinstance(item, dict):
                        fetch_rows.append({
                            "terminal_event_id": terminal_event_id,
                            "tool_call_id": tool_call_id,
                            "item_index": idx,
                            "input_url": item.get("input_url") or item.get("url"),
                            "normalized_url": item.get("normalized_url"),
                            "fetched_url": item.get("fetched_url"),
                            "source_type": item.get("source_type"),
                            "fetch_backend": item.get("fetch_backend") or item.get("origin_backend"),
                            "status": item.get("status") or status,
                            "content_length": item.get("content_length"),
                            "page_char_count": item.get("page_char_count"),
                            "word_count": item.get("word_count"),
                            "window_offset": item.get("window_offset"),
                            "window_length": item.get("window_length"),
                            "window_returned_chars": item.get("window_returned_chars"),
                            "window_total_chars": item.get("window_total_chars"),
                            "window_has_more": item.get("window_has_more"),
                            "window_next_offset": item.get("window_next_offset"),
                            "item_duration_ms": item.get("duration_ms"),
                            "payload_json": item,
                        })
                        summary_data = item.get("summary")
                        if isinstance(summary_data, dict):
                            summary_rows.append({
                                "terminal_event_id": terminal_event_id,
                                "tool_call_id": tool_call_id,
                                "item_index": idx,
                                "normalized_url": item.get("normalized_url"),
                                "focus_query": fields.get("focus_query"),
                                "input_chars": item.get("page_char_count") or (
                                    len(str(item.get("page_content") or ""))
                                    if item.get("page_content")
                                    else None
                                ),
                                "source_url_count": 1,
                                "is_batch": True,
                                "batch_size": len(items_data),
                                "is_stub": bool(summary_data.get("is_stub") or not summary_data.get("summary")),
                                "backend": summary_data.get("backend"),
                                "model_requested": summary_data.get("model_requested"),
                                "model_used": summary_data.get("model_used") or summary_data.get("model"),
                                "fallback_attempted": summary_data.get("fallback_attempted"),
                                "fallback_tier": summary_data.get("fallback_tier"),
                                "input_tokens": summary_data.get("input_tokens"),
                                "output_tokens": summary_data.get("output_tokens"),
                                "total_tokens": summary_data.get("total_tokens"),
                                "summary_length_chars": (
                                    len(summary_data.get("summary") or "")
                                    if summary_data.get("summary")
                                    else 0
                                ),
                                "key_points_count": (
                                    len(summary_data.get("key_points", []))
                                    if isinstance(summary_data.get("key_points"), list)
                                    else 0
                                ),
                                "important_entities_count": (
                                    len(summary_data.get("important_entities", []))
                                    if isinstance(summary_data.get("important_entities"), list)
                                    else 0
                                ),
                                "verbatim_terms_count": (
                                    len(summary_data.get("verbatim_terms", []))
                                    if isinstance(summary_data.get("verbatim_terms"), list)
                                    else 0
                                ),
                                "limitations_count": (
                                    len(summary_data.get("limitations", []))
                                    if isinstance(summary_data.get("limitations"), list)
                                    else 0
                                ),
                                "source_date": summary_data.get("source_date"),
                                "status": status,
                                "error_type": fields.get("error_type"),
                                "error_message": error_message,
                                "duration_ms": None,
                                "payload_json": summary_data,
                            })

        insert_content_operation_batches(
            content_operations=[op_row],
            content_fetches=fetch_rows,
            content_summaries=summary_rows,
        )
    except Exception as exc:
        logger.debug("Failed to persist content analytics: %s", exc)


def _persist_tool_output_items(
    *,
    tool_name: str,
    tool_call_id: str,
    fields: dict[str, Any],
    logger: logging.Logger,
) -> None:
    """Persist output items from tool responses for cross-tool linkage."""
    try:
        from ..analytics.duckdb_store import insert_funnel_uplift_batches
        from ..analytics.observability_store import _canonical_result_id as _cri

        rows: list[dict[str, Any]] = []
        run_key = get_current_run_key()
        session_id = fields.get("session_id")

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
                rows.append({
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
                })
        elif tool_name in ("get_content", "batch_get_content"):
            url = fields.get("url") or fields.get("input_url") or ""
            if url:
                rows.append({
                    "output_item_id": _cri(f"{tool_call_id}|content|1"),
                    "tool_call_id": tool_call_id,
                    "session_id": session_id,
                    "run_key": run_key,
                    "tool_name": tool_name,
                    "item_type": "content",
                    "item_rank": 1,
                    "canonical_result_id": _cri(url),
                    "raw_url": url,
                    "title": None,
                    "snippet": None,
                })
        elif tool_name == "gemini_search":
            sources = fields.get("sources") or []
            for rank, src in enumerate(sources, start=1):
                if isinstance(src, dict):
                    url = src.get("url") or ""
                    title = src.get("title") or ""
                else:
                    url = getattr(src, "url", "") or ""
                    title = getattr(src, "title", "") or ""
                rows.append({
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
                })

        if rows:
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
        insert_tool_call_event(
            event_id=event_id,
            tool_call_id=tool_call_id,
            run_key=get_current_run_key(),
            session_id=fields.get("session_id"),
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
            model=fields.get("model") or fields.get("model_used") or fields.get("client_model"),
            input_tokens=fields.get("input_tokens"),
            output_tokens=fields.get("output_tokens"),
            request_fingerprint=payload.get("request_fingerprint"),
            error_type=fields.get("error_type"),
            error_message=error_message,
            payload_json=payload_with_context,
        )
    except Exception as exc:  # pragma: no cover - best-effort sink
        logger.debug("DuckDB tool-call sink failed for %s: %s", tool_name, exc)

    if phase in ("response", "error"):
        if tool_name == "quick_web_search":
            _persist_quick_web_search_analytics(
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
        elif tool_name in ("get_content", "batch_get_content"):
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
        if tool_name in (
            "web_search", "get_content", "batch_get_content", "gemini_search"
        ):
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
