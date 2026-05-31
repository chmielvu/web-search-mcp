"""DuckDB-backed append-only event store for search tuning."""

from __future__ import annotations

import json
import threading
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
        event_name,
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
        json.dumps(payload, ensure_ascii=False, default=str),
    )

    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                    event_name VARCHAR,
                    recorded_at TIMESTAMP,
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
                    payload_json VARCHAR
                )
                """
            )
            connection.execute(
                f"""
                INSERT INTO {_TABLE_NAME} (
                    event_name,
                    recorded_at,
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
                    payload_json
                ) VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                record,
            )
        finally:
            connection.close()
