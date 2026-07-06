from __future__ import annotations

from typing import Any

import duckdb

from ..settings import settings
from .async_writes import dispatch_duckdb_write
from .duckdb_store import _LOCK, _db_path
from .observability_schema import (
    _BRANCH_ATTEMPTS_TABLE,
    _BRANCH_CANDIDATES_TABLE,
    _PIPELINE_HEARTBEATS_TABLE,
    _PROVIDER_HEALTH_TABLE,
    _RESPONSE_RESULTS_TABLE,
    _TOOL_CALLS_TABLE,
    ensure_pipeline_observability_tables,
)


def _insert(
    *,
    table: str,
    columns: list[str],
    values: list[Any],
    db_path: str | None = None,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)

    def _write() -> None:
        ensure_pipeline_observability_tables(db_path=db_path)
        with _LOCK:
            connection = duckdb.connect(str(path))
            try:
                connection.execute(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                    values,
                )
            finally:
                connection.close()

    dispatch_duckdb_write(f"analytics.{table}", _write)


def insert_web_search_tool_call(*, db_path: str | None = None, **kwargs: Any) -> None:
    columns = [
        "tool_call_id",
        "run_key",
        "cache_hit",
        "query",
        "normalized_query",
        "research_goal",
        "rewrite_enabled",
        "result_offset",
        "num_results_requested",
        "num_results_returned",
        "cache_identity",
        "providers_requested",
        "providers_used",
        "search_options_json",
        "response_json",
        "trace_id",
        "span_id",
        "payload_json",
    ]
    _insert(
        table=_TOOL_CALLS_TABLE,
        columns=columns,
        values=[kwargs.get(col) for col in columns],
        db_path=db_path,
    )


def insert_web_search_response_results(*, db_path: str | None = None, **kwargs: Any) -> None:
    columns = [
        "tool_call_id",
        "run_key",
        "cache_hit",
        "result_rank",
        "title",
        "link",
        "snippet",
        "domain",
        "providers",
        "provider_count",
        "score",
        "candidate_id",
        "canonical_result_id",
        "payload_json",
    ]
    _insert(
        table=_RESPONSE_RESULTS_TABLE,
        columns=columns,
        values=[kwargs.get(col) for col in columns],
        db_path=db_path,
    )


def insert_branch_attempts(*, db_path: str | None = None, **kwargs: Any) -> None:
    columns = [
        "branch_attempt_id",
        "run_key",
        "tool_call_id",
        "branch_index",
        "branch_type",
        "branch_query",
        "branch_weight",
        "provider_names",
        "provider_count",
        "status",
        "deadline_seconds",
        "latency_ms",
        "result_count",
        "error_type",
        "error_message",
        "payload_json",
    ]
    _insert(
        table=_BRANCH_ATTEMPTS_TABLE,
        columns=columns,
        values=[kwargs.get(col) for col in columns],
        db_path=db_path,
    )


def insert_branch_candidates(*, db_path: str | None = None, **kwargs: Any) -> None:
    columns = [
        "run_key",
        "branch_attempt_id",
        "branch_index",
        "candidate_rank",
        "title",
        "link",
        "snippet",
        "domain",
        "providers",
        "provider_count",
        "score",
        "candidate_id",
        "canonical_result_id",
        "payload_json",
    ]
    _insert(
        table=_BRANCH_CANDIDATES_TABLE,
        columns=columns,
        values=[kwargs.get(col) for col in columns],
        db_path=db_path,
    )


def insert_provider_health_transition(*, db_path: str | None = None, **kwargs: Any) -> None:
    columns = [
        "provider",
        "transition",
        "run_key",
        "tool_call_id",
        "status",
        "consecutive_failures",
        "cooldown_seconds",
        "cooldown_remaining_s",
        "total_successes",
        "total_failures",
        "error_type",
        "is_rate_limit",
        "circuit_state",
        "payload_json",
    ]
    _insert(
        table=_PROVIDER_HEALTH_TABLE,
        columns=columns,
        values=[kwargs.get(col) for col in columns],
        db_path=db_path,
    )


def insert_pipeline_heartbeat(*, db_path: str | None = None, **kwargs: Any) -> None:
    columns = [
        "run_key",
        "tool_call_id",
        "stage",
        "duration_ms",
        "branch_count",
        "provider_count",
        "merged_count",
        "reranked_count",
        "final_count",
        "returned_count",
        "cache_hit",
        "payload_json",
    ]
    _insert(
        table=_PIPELINE_HEARTBEATS_TABLE,
        columns=columns,
        values=[kwargs.get(col) for col in columns],
        db_path=db_path,
    )
