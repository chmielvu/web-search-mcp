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


def _db_path(db_path: str | None = None) -> Path:
    return Path(db_path or settings.analytics_duckdb_path)


def _event_value(payload: dict[str, Any], key: str) -> str | int | float | None:
    value = payload.get(key)
    if value is None or isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


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
        _event_value(payload, "provider"),
        _event_value(payload, "model"),
        _event_value(payload, "duration_ms"),
        _event_value(payload, "input_count"),
        _event_value(payload, "output_count"),
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
