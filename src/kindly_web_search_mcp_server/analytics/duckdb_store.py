"""DuckDB-backed append-only event store for search tuning."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

import duckdb

from ..settings import settings

_LOCK = threading.Lock()
_TABLE_NAME = "search_events"
_RUNS_TABLE_NAME = "search_runs"


def _db_path(db_path: str | None = None) -> Path:
    return Path(db_path or settings.analytics_duckdb_path)


def _event_value(payload: dict[str, Any], key: str) -> str | int | float | None:
    value = payload.get(key)
    if value is None or isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _provider_value(payload: dict[str, Any]) -> str | None:
    value = payload.get("provider")
    if value is None:
        value = payload.get("provider_name")
    return value if isinstance(value, str) else None


def _int_value(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None


def _run_key(payload: dict[str, Any]) -> str | None:
    trace_id = payload.get("trace_id")
    if isinstance(trace_id, str) and trace_id:
        return trace_id
    fingerprint = payload.get("request_fingerprint")
    if isinstance(fingerprint, str) and fingerprint:
        return fingerprint
    return None


def _phase(event_name: str) -> str | None:
    parts = event_name.rsplit(".", 1)
    return parts[1] if len(parts) == 2 else None


def _duration_ms_value(payload: dict[str, Any]) -> float | None:
    value = payload.get("duration_ms")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    duration_seconds = payload.get("duration_seconds")
    if isinstance(duration_seconds, (int, float)) and not isinstance(
        duration_seconds, bool
    ):
        return round(float(duration_seconds) * 1000.0, 3)
    return None


def _input_count_value(payload: dict[str, Any]) -> int | None:
    value = _int_value(
        payload,
        (
            "input_count",
            "input_result_count",
            "input_list_count",
            "num_results_requested",
            "num_results",
            "tool_calls_count",
        ),
    )
    return value


def _output_count_value(payload: dict[str, Any]) -> int | None:
    value = _int_value(
        payload,
        (
            "output_count",
            "result_count",
            "merged_result_count",
            "final_result_count",
            "output_result_count",
            "total_returned",
            "success_count",
            "sources_count",
        ),
    )
    return value


def _ensure_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
            event_id VARCHAR,
            event_name VARCHAR,
            recorded_at TIMESTAMP,
            run_key VARCHAR,
            tool_name VARCHAR,
            phase VARCHAR,
            query VARCHAR,
            normalized_query VARCHAR,
            research_goal VARCHAR,
            provider VARCHAR,
            model VARCHAR,
            duration_ms DOUBLE,
            input_count INTEGER,
            output_count INTEGER,
            trace_id VARCHAR,
            span_id VARCHAR,
            cache_hit VARCHAR,
            payload_json VARCHAR
        )
        """
    )
    existing = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({_TABLE_NAME})").fetchall()
    }
    additions = {
        "event_id": "VARCHAR",
        "run_key": "VARCHAR",
        "tool_name": "VARCHAR",
        "phase": "VARCHAR",
        "cache_hit": "VARCHAR",
    }
    for column, column_type in additions.items():
        if column not in existing:
            connection.execute(
                f"ALTER TABLE {_TABLE_NAME} ADD COLUMN {column} {column_type}"
            )
    connection.execute(
        f"UPDATE {_TABLE_NAME} SET event_id = uuid()::VARCHAR WHERE event_id IS NULL"
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET run_key = coalesce(trace_id, json_extract_string(payload_json, '$.request_fingerprint'))
        WHERE run_key IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET tool_name = json_extract_string(payload_json, '$.tool_name')
        WHERE tool_name IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET phase = regexp_extract(event_name, '[^.]+$', 0)
        WHERE phase IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET provider = coalesce(
            provider,
            json_extract_string(payload_json, '$.provider'),
            json_extract_string(payload_json, '$.provider_name')
        )
        WHERE provider IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET input_count = coalesce(
            input_count,
            CAST(json_extract_string(payload_json, '$.input_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.input_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.input_list_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.num_results_requested') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.num_results') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.tool_calls_count') AS INTEGER)
        )
        WHERE input_count IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET output_count = coalesce(
            output_count,
            CAST(json_extract_string(payload_json, '$.output_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.merged_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.final_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.output_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.total_returned') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.success_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.sources_count') AS INTEGER)
        )
        WHERE output_count IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET duration_ms = coalesce(
            duration_ms,
            CAST(json_extract_string(payload_json, '$.duration_ms') AS DOUBLE),
            CAST(json_extract_string(payload_json, '$.duration_seconds') AS DOUBLE) * 1000.0
        )
        WHERE duration_ms IS NULL
        """
    )


def ensure_store_schema(*, db_path: str | None = None) -> None:
    path = _db_path(db_path)
    if not path.exists():
        return
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_schema(connection)
        finally:
            connection.close()


def _ensure_search_runs(connection: duckdb.DuckDBPyConnection) -> None:
    """Create search_runs table with indexes if it doesn't exist."""
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RUNS_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            query VARCHAR NOT NULL,
            normalized_query VARCHAR,
            research_goal VARCHAR,
            num_results_requested INTEGER,
            rewrite_enabled BOOLEAN,
            session_id VARCHAR,
            tool_name VARCHAR DEFAULT 'web_search',
            duration_ms DOUBLE,
            final_result_count INTEGER,
            candidate_count INTEGER,
            has_more BOOLEAN,
            result_offset INTEGER,
            status VARCHAR,
            error_type VARCHAR,
            payload_json JSON
        )
        """
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_runs_run_key ON {_RUNS_TABLE_NAME}(run_key)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_runs_recorded_at ON {_RUNS_TABLE_NAME}(recorded_at)"
    )


def insert_search_run(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    """Insert a row into the search_runs table.

    Uses the same pattern as append_event()
    (threading.Lock, duckdb.connect, execute INSERT with VALUES).
    """

    if not settings.analytics_enabled:
        return

    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "run_key",
        "query",
        "normalized_query",
        "research_goal",
        "num_results_requested",
        "rewrite_enabled",
        "session_id",
        "tool_name",
        "duration_ms",
        "final_result_count",
        "candidate_count",
        "has_more",
        "result_offset",
        "status",
        "error_type",
        "payload_json",
    ]

    # Apply Python-level defaults for columns with SQL DEFAULT values
    # so DuckDB doesn't get an explicit None that bypasses the DEFAULT.
    if kwargs.get("tool_name") is None:
        kwargs["tool_name"] = "web_search"

    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)

    values = [kwargs.get(col) for col in columns]

    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_search_runs(connection)
            connection.execute(
                f"""
                INSERT INTO {_RUNS_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                """,
                values,
            )
        finally:
            connection.close()


def append_event(
    event_name: str,
    payload: dict[str, Any],
    *,
    db_path: str | None = None,
) -> None:
    """Append a normalized observability payload to DuckDB.

    The store is best-effort and is disabled when
    `KINDLY_ANALYTICS_ENABLED=false`.
    """

    if not settings.analytics_enabled:
        return

    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    record = (
        str(uuid.uuid4()),
        event_name,
        _run_key(payload),
        _event_value(payload, "tool_name"),
        _phase(event_name),
        _event_value(payload, "query"),
        _event_value(payload, "normalized_query"),
        _event_value(payload, "research_goal"),
        _provider_value(payload),
        _event_value(payload, "model"),
        _duration_ms_value(payload),
        _input_count_value(payload),
        _output_count_value(payload),
        _event_value(payload, "trace_id"),
        _event_value(payload, "span_id"),
        _event_value(payload, "cache_hit"),
        json.dumps(payload, ensure_ascii=False, default=str),
    )

    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_schema(connection)
            connection.execute(
                f"""
                INSERT INTO {_TABLE_NAME} (
                    event_id,
                    event_name,
                    recorded_at,
                    run_key,
                    tool_name,
                    phase,
                    query,
                    normalized_query,
                    research_goal,
                    provider,
                    model,
                    duration_ms,
                    input_count,
                    output_count,
                    trace_id,
                    span_id,
                    cache_hit,
                    payload_json
                ) VALUES (
                    ?,
                    ?,
                    CURRENT_TIMESTAMP,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,
                record,
            )
        finally:
            connection.close()
