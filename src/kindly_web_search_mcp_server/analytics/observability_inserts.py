"""Legacy observability inserts — stripped to provider_health_transitions only.

The 5 other insert functions (web_search_tool_calls, web_search_response_results,
branch_attempts, branch_candidates, pipeline_heartbeats) targeted tables dropped
in the clean-cutover redesign. Their functionality is now covered by the unified
9-table schema in ``writers/`` via ``duckdb_store`` re-exports.
"""

from __future__ import annotations

from typing import Any

import duckdb

from ..settings import settings
from .async_writes import dispatch_duckdb_write
from .duckdb_store import _LOCK, _db_path
from .observability_schema import (
    _PROVIDER_HEALTH_TABLE,
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
