"""Persistence helper for code_search tool outcomes.

Moved from ``utils/observability.py`` during the analytics/utils cutover.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["_persist_code_search_analytics"]


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
        from ..writers import insert_code_search_batches

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
            "provider_response_count": len(getattr(response, "provider_summaries", []) or []),
            "provider_hit_counts": getattr(stats, "provider_counts", None),
            "request_count": getattr(stats, "request_count", None),
            "hydration_count": getattr(stats, "hydration_count", None),
            "rerank_count": getattr(stats, "rerank_count", None),
            "returned_count": len(getattr(response, "results", []) or []),
            "repository_count": len(getattr(response, "repositories", []) or []),
            "diagnostic_count": len(getattr(response, "diagnostics", []) or []),
            "truncated": getattr(stats, "truncated", None),
            "dropped_count": getattr(stats, "dropped_count", None),
            "estimated_output_tokens": getattr(stats, "estimated_tokens", None),
            "duration_ms": fields.get("duration_ms"),
            "outcome": getattr(response, "outcome", status),
            "error_type": fields.get("error_type"),
            "error_message": error_message,
            "payload_json": payload_json,
        }

        provider_rows = []
        for idx, provider_summary in enumerate(getattr(response, "provider_summaries", []) or []):
            provider_rows.append(
                {
                    "terminal_event_id": terminal_event_id,
                    "response_index": idx,
                    "provider": provider_summary.get("provider"),
                    "hit_count": provider_summary.get("hit_count", 0),
                    "request_count": provider_summary.get("request_count", 0),
                    "outcome": provider_summary.get("outcome"),
                    "compiled_queries": provider_summary.get("compiled_queries"),
                    "duration_ms": provider_summary.get("duration_ms"),
                    "error_type": provider_summary.get("error_type"),
                    "error_message": provider_summary.get("error_message"),
                    "payload_json": provider_summary.get("payload_json"),
                }
            )

        diagnostic_rows = []
        for idx, d in enumerate(getattr(response, "diagnostics", []) or []):
            diagnostic_rows.append(
                {
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
                }
            )

        hit_rows = []
        hit_variant_rows = []
        for hit_rank, hit in enumerate(getattr(response, "results", []) or [], 1):
            location = getattr(hit, "location", None)
            hit_rows.append(
                {
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
                    "snippet": getattr(hit, "source_window", None),
                    "published_date": getattr(hit, "published_date", None),
                    "final_score": getattr(hit, "score", None),
                    "score_components": getattr(hit, "score_components", None),
                    "reasons": getattr(hit, "reasons", None),
                    "hydrated": bool(getattr(hit, "source_window", None)),
                    "hydrated_source_truncated": False,
                    "line_start": getattr(hit, "line_start", None),
                    "line_end": getattr(hit, "line_end", None),
                    "commit_oid": getattr(hit, "commit_oid", None),
                    "fragment_count": 1 if getattr(hit, "source_window", None) else 0,
                    "symbol_count": len(getattr(hit, "symbols", []) or []),
                    "match_span_count": len(getattr(hit, "match_lines", []) or []),
                    "location_precision": getattr(location, "precision", "unknown"),
                    "lines_available": getattr(location, "lines_available", False),
                    "revision_available": getattr(location, "revision_available", False),
                    "match_data_available": getattr(location, "match_data_available", False),
                    "source_metadata": getattr(hit, "source_metadata", None),
                    "payload_json": None,
                }
            )
            if getattr(hit, "query_variant", None):
                hit_variant_rows.append(
                    {
                        "terminal_event_id": terminal_event_id,
                        "hit_rank": hit_rank,
                        "association_index": 0,
                        "variant_index": None,
                        "provider": getattr(hit, "provider", None),
                        "query_variant": getattr(hit, "query_variant", None),
                        "search_rank": getattr(hit, "search_rank", None),
                    }
                )

        query_variant_rows = []
        variants = getattr(plan, "variants", []) or []
        variant_kinds = getattr(plan_meta, "variant_kinds", []) or []
        for v_idx, q_text in enumerate(variants):
            q_kind = variant_kinds[v_idx] if v_idx < len(variant_kinds) else None
            query_variant_rows.append(
                {
                    "terminal_event_id": terminal_event_id,
                    "variant_index": v_idx,
                    "query_text": q_text,
                    "variant_kind": q_kind,
                }
            )

        repo_rows = []
        for r_idx, repo in enumerate(getattr(response, "repositories", []) or []):
            repo_rows.append(
                {
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
                }
            )

        rerank_rows = []
        if stats and getattr(stats, "rerank_count", 0) > 0:
            rerank_rows.append(
                {
                    "terminal_event_id": terminal_event_id,
                    "provider": getattr(stats, "rerank_provider", None),
                    "model": getattr(stats, "rerank_model", None),
                    "input_count": getattr(stats, "rerank_input_count", None),
                    "output_count": getattr(stats, "rerank_output_count", None),
                    "reranked_count": getattr(stats, "rerank_count", 0),
                    "status": getattr(stats, "rerank_status", None),
                    "diagnostic_outcome": getattr(stats, "rerank_diagnostic_outcome", None),
                    "diagnostic_message": getattr(stats, "rerank_diagnostic_message", None),
                    "duration_ms": getattr(stats, "rerank_duration_ms", None),
                    "payload_json": getattr(stats, "rerank_payload", None),
                }
            )
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
