from __future__ import annotations

import duckdb

from ..settings import settings
from .duckdb_store import _LOCK, _db_path

_TOOL_CALLS_TABLE = "web_search_tool_calls"
_RESPONSE_RESULTS_TABLE = "web_search_response_results"
_BRANCH_ATTEMPTS_TABLE = "branch_attempts"
_BRANCH_CANDIDATES_TABLE = "branch_candidates"
_PROVIDER_HEALTH_TABLE = "provider_health_transitions"
_PIPELINE_HEARTBEATS_TABLE = "pipeline_heartbeats"


def _ensure_web_search_tool_calls(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TOOL_CALLS_TABLE} (
            tool_call_id VARCHAR NOT NULL,
            run_key VARCHAR,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            cache_hit VARCHAR,
            query VARCHAR NOT NULL,
            normalized_query VARCHAR,
            research_goal VARCHAR,
            rewrite_enabled BOOLEAN,
            result_offset INTEGER,
            num_results_requested INTEGER,
            num_results_returned INTEGER,
            cache_identity VARCHAR,
            providers_requested VARCHAR[],
            providers_used VARCHAR[],
            search_options_json JSON,
            response_json JSON,
            trace_id VARCHAR,
            span_id VARCHAR,
            payload_json JSON
        )
        """
    )


def _ensure_web_search_response_results(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RESPONSE_RESULTS_TABLE} (
            tool_call_id VARCHAR NOT NULL,
            run_key VARCHAR,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            cache_hit VARCHAR,
            result_rank INTEGER NOT NULL,
            title VARCHAR NOT NULL,
            link VARCHAR NOT NULL,
            snippet VARCHAR NOT NULL,
            domain VARCHAR,
            providers VARCHAR[],
            provider_count INTEGER,
            score DOUBLE,
            candidate_id VARCHAR,
            canonical_result_id VARCHAR,
            payload_json JSON
        )
        """
    )


def _ensure_branch_attempts(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_BRANCH_ATTEMPTS_TABLE} (
            branch_attempt_id VARCHAR NOT NULL,
            run_key VARCHAR NOT NULL,
            tool_call_id VARCHAR,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            branch_index INTEGER NOT NULL,
            branch_type VARCHAR NOT NULL,
            branch_query VARCHAR NOT NULL,
            branch_weight DOUBLE,
            provider_names VARCHAR[],
            provider_count INTEGER,
            status VARCHAR NOT NULL,
            deadline_seconds DOUBLE,
            latency_ms DOUBLE,
            result_count INTEGER,
            error_type VARCHAR,
            error_message VARCHAR,
            payload_json JSON
        )
        """
    )


def _ensure_branch_candidates(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_BRANCH_CANDIDATES_TABLE} (
            run_key VARCHAR NOT NULL,
            branch_attempt_id VARCHAR NOT NULL,
            branch_index INTEGER NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            candidate_rank INTEGER NOT NULL,
            title VARCHAR NOT NULL,
            link VARCHAR NOT NULL,
            snippet VARCHAR NOT NULL,
            domain VARCHAR,
            providers VARCHAR[],
            provider_count INTEGER,
            score DOUBLE,
            candidate_id VARCHAR,
            canonical_result_id VARCHAR,
            payload_json JSON
        )
        """
    )


def _ensure_provider_health_transitions(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_PROVIDER_HEALTH_TABLE} (
            provider VARCHAR NOT NULL,
            transition VARCHAR NOT NULL,
            run_key VARCHAR,
            tool_call_id VARCHAR,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            status VARCHAR,
            consecutive_failures INTEGER,
            cooldown_seconds DOUBLE,
            cooldown_remaining_s DOUBLE,
            total_successes INTEGER,
            total_failures INTEGER,
            error_type VARCHAR,
            is_rate_limit BOOLEAN,
            circuit_state VARCHAR,
            payload_json JSON
        )
        """
    )


def _ensure_pipeline_heartbeats(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_PIPELINE_HEARTBEATS_TABLE} (
            run_key VARCHAR NOT NULL,
            tool_call_id VARCHAR,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            stage VARCHAR NOT NULL,
            duration_ms DOUBLE,
            branch_count INTEGER,
            provider_count INTEGER,
            merged_count INTEGER,
            reranked_count INTEGER,
            final_count INTEGER,
            returned_count INTEGER,
            cache_hit VARCHAR,
            payload_json JSON
        )
        """
    )


def ensure_pipeline_observability_tables(*, db_path: str | None = None) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_web_search_tool_calls(connection)
            _ensure_web_search_response_results(connection)
            _ensure_branch_attempts(connection)
            _ensure_branch_candidates(connection)
            _ensure_provider_health_transitions(connection)
            _ensure_pipeline_heartbeats(connection)
        finally:
            connection.close()

