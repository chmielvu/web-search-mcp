"""TableWriter abstraction and public insert wrappers."""

from __future__ import annotations

from collections.abc import Callable
import json
import uuid
from typing import Any

import duckdb

from ...llm.usage import extract_llm_usage
from ...settings import settings
from .table_names import _TABLE_NAME

from ..async_writes import dispatch_duckdb_write
from .connection import _db_path, _LOCK

_FACADE_MODULE = "kindly_web_search_mcp_server.analytics.duckdb_store"


def _resolve_ensure(name: str) -> Callable[[duckdb.DuckDBPyConnection], None]:
    """Resolve an ``_ensure_*`` callable on the facade module at call time.

    Tests patch ``duckdb_store._ensure_schema`` / ``duckdb_store._ensure_*`` to
    stub out schema setup, so writers look up the callable through the facade at
    insert time rather than holding a direct reference.
    """
    import sys

    return getattr(sys.modules[_FACADE_MODULE], name)


class TableWriter:
    """Encapsulates the connect / lock / ensure / insert / close pattern."""

    def __init__(
        self,
        *,
        table_name: str,
        ensure_name: str,
        columns: list[str],
        task_name: str,
        defaults: dict[str, Any] | None = None,
        on_conflict: str = "",
    ) -> None:
        self.table_name = table_name
        self.ensure_name = ensure_name
        self.columns = columns
        self.task_name = task_name
        self.defaults = defaults or {}
        placeholders = ", ".join("?" for _ in columns)
        col_list = ", ".join(columns)
        sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"
        if on_conflict:
            sql = f"{sql} {on_conflict}"
        self.insert_sql = sql

    def insert(self, *, db_path: str | None = None, **kwargs: Any) -> None:
        """Insert a single row from the supplied keyword values."""
        from ...settings import settings

        if not settings.analytics_enabled:
            return
        path = _db_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        for key, value in self.defaults.items():
            if kwargs.get(key) is None:
                kwargs[key] = value
        values = [kwargs.get(col) for col in self.columns]
        ensure_name = self.ensure_name
        insert_sql = self.insert_sql
        task_name = self.task_name

        def _write() -> None:
            with _LOCK:
                connection = duckdb.connect(str(path))
                try:
                    _resolve_ensure(ensure_name)(connection)
                    connection.execute(insert_sql, values)
                finally:
                    connection.close()

        dispatch_duckdb_write(task_name, _write)


def insert_search_run(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _SEARCH_RUN_WRITER

    _SEARCH_RUN_WRITER.insert(db_path=db_path, **kwargs)


def insert_query_understanding(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _QUERY_UNDERSTANDING_WRITER

    _QUERY_UNDERSTANDING_WRITER.insert(db_path=db_path, **kwargs)


def insert_query_rewrites(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _QUERY_REWRITES_WRITER

    _QUERY_REWRITES_WRITER.insert(db_path=db_path, **kwargs)


def insert_provider_calls(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _PROVIDER_CALLS_WRITER

    _PROVIDER_CALLS_WRITER.insert(db_path=db_path, **kwargs)


def insert_provider_candidates(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _PROVIDER_CANDIDATES_WRITER

    _PROVIDER_CANDIDATES_WRITER.insert(db_path=db_path, **kwargs)


def insert_merged_candidates(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _MERGED_CANDIDATES_WRITER

    _MERGED_CANDIDATES_WRITER.insert(db_path=db_path, **kwargs)


def insert_rerank_stages(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _RERANK_STAGES_WRITER

    _RERANK_STAGES_WRITER.insert(db_path=db_path, **kwargs)


def insert_rerank_candidates(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _RERANK_CANDIDATES_WRITER

    _RERANK_CANDIDATES_WRITER.insert(db_path=db_path, **kwargs)


def insert_final_results(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _FINAL_RESULTS_WRITER

    _FINAL_RESULTS_WRITER.insert(db_path=db_path, **kwargs)


def insert_search_quality_scores(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _SEARCH_QUALITY_SCORES_WRITER

    _SEARCH_QUALITY_SCORES_WRITER.insert(db_path=db_path, **kwargs)


def insert_judge_evaluation(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _JUDGE_EVALUATION_WRITER

    _JUDGE_EVALUATION_WRITER.insert(db_path=db_path, **kwargs)


def insert_ab_experiment(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _AB_EXPERIMENT_WRITER

    _AB_EXPERIMENT_WRITER.insert(db_path=db_path, **kwargs)


def insert_ab_shadow_run(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _AB_SHADOW_RUN_WRITER

    _AB_SHADOW_RUN_WRITER.insert(db_path=db_path, **kwargs)


# ---------------------------------------------------------------------------
# Value-extraction helpers used by append_event
# ---------------------------------------------------------------------------
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


def _model_used_value(payload: dict[str, Any]) -> str | None:
    value = payload.get("model_used")
    if value is None:
        value = payload.get("model")
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
    # Check run_key first (explicit search run key)
    run_key = payload.get("run_key")
    if isinstance(run_key, str) and run_key:
        return run_key
    # Fallback: use trace_id or request_fingerprint
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
    if isinstance(duration_seconds, (int, float)) and not isinstance(duration_seconds, bool):
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


def _input_tokens_value(payload: dict[str, Any]) -> int | None:
    usage = extract_llm_usage(payload)
    return usage.input_tokens if usage else None


def _output_tokens_value(payload: dict[str, Any]) -> int | None:
    usage = extract_llm_usage(payload)
    return usage.output_tokens if usage else None


def append_event(
    event_name: str,
    payload: dict[str, Any],
    *,
    db_path: str | None = None,
) -> None:
    """Append a normalized observability payload to DuckDB.

    The store is best-effort and is disabled when
    `ANALYTICS_ENABLED=false`.
    """

    if not settings.analytics_enabled:
        return

    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    usage = extract_llm_usage(payload)
    model_used = _model_used_value(payload)

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
        model_used,
        model_used,
        _duration_ms_value(payload),
        _input_count_value(payload),
        _output_count_value(payload),
        usage.input_tokens if usage else _input_tokens_value(payload),
        usage.output_tokens if usage else _output_tokens_value(payload),
        _event_value(payload, "trace_id"),
        _event_value(payload, "span_id"),
        _event_value(payload, "cache_hit"),
        json.dumps(payload, ensure_ascii=False, default=str),
    )

    def _write() -> None:
        with _LOCK:
            connection = duckdb.connect(str(path))
            try:
                _resolve_ensure("_ensure_schema")(connection)
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
                        model_used,
                        duration_ms,
                        input_count,
                        output_count,
                        input_tokens,
                        output_tokens,
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

    dispatch_duckdb_write("analytics.search_events", _write)
