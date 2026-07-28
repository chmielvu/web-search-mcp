"""TableWriter abstraction and public insert wrappers."""

from __future__ import annotations

import json
import sys
from uuid import uuid4
from collections.abc import Callable
from typing import Any

import duckdb

from .connection import _db_path, _LOCK
from ..async_writes import dispatch_duckdb_write
from ...telemetry.usage import extract_llm_usage

_FACADE_MODULE = "kindly_web_search_mcp_server.analytics.duckdb_store"


def _resolve_ensure(name: str) -> Callable[[duckdb.DuckDBPyConnection], None]:
    """Resolve an ``_ensure_*`` callable on the facade module at call time.

    This avoids circular imports: the facade imports from ``writers`` which
    imports from ``core``; resolving at call-time breaks the cycle.
    """
    return getattr(sys.modules[_FACADE_MODULE], name)


class TableWriter:
    """Encapsulates the connect / lock / ensure / insert / close pattern."""

    def __init__(
        self,
        table_name: str,
        ensure_name: str,
        columns: list[str],
        defaults: dict[str, Any] | None = None,
        on_conflict: str | None = None,
        task_name: str | None = None,
    ) -> None:
        self.table_name = table_name
        self.ensure_name = ensure_name
        self.columns = columns
        self.defaults = defaults or {}
        self.on_conflict = on_conflict or ""
        self.task_name = task_name or f"analytics.{table_name}"

    def insert(
        self,
        *,
        db_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Insert one row synchronously (called inside dispatch_duckdb_write)."""
        path = _db_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            connection = duckdb.connect(str(path))
            try:
                _resolve_ensure(self.ensure_name)(connection)
                col_list = ", ".join(self.columns)
                placeholders = ", ".join(["?"] * len(self.columns))
                conflict = f" {self.on_conflict}" if self.on_conflict else ""
                connection.execute(
                    f"INSERT INTO {self.table_name} ({col_list}) VALUES ({placeholders}){conflict}",
                    [kwargs.get(c, self.defaults.get(c)) for c in self.columns],
                )
            finally:
                connection.close()

    def insert_batch(
        self,
        rows: list[dict[str, Any]],
        *,
        db_path: str | None = None,
    ) -> None:
        """Insert multiple rows in a single connection."""
        if not rows:
            return
        path = _db_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            connection = duckdb.connect(str(path))
            try:
                _resolve_ensure(self.ensure_name)(connection)
                col_list = ", ".join(self.columns)
                placeholders = ", ".join(["?"] * len(self.columns))
                conflict = f" {self.on_conflict}" if self.on_conflict else ""
                sql = (
                    f"INSERT INTO {self.table_name} ({col_list}) VALUES ({placeholders}){conflict}"
                )
                connection.executemany(
                    sql,
                    [[r.get(c, self.defaults.get(c)) for c in self.columns] for r in rows],
                )
            finally:
                connection.close()

    def dispatch_insert(
        self,
        *,
        db_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Fire-and-forget insert via the dedicated DuckDB write executor."""
        task_name = self.task_name

        def _write() -> None:
            self.insert(db_path=db_path, **kwargs)

        dispatch_duckdb_write(task_name, _write)

    def dispatch_insert_batch(
        self,
        rows: list[dict[str, Any]],
        *,
        db_path: str | None = None,
    ) -> None:
        if not rows:
            return
        task_name = self.task_name

        def _write() -> None:
            self.insert_batch(rows, db_path=db_path)

        dispatch_duckdb_write(task_name, _write)


# ---------------------------------------------------------------------------
# Public insert wrappers (lazy-import writers to avoid circular imports)
# ---------------------------------------------------------------------------
def insert_search_run(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _SEARCH_RUN_WRITER

    _SEARCH_RUN_WRITER.insert(db_path=db_path, **kwargs)


def insert_search_branches(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _SEARCH_BRANCHES_WRITER

    _SEARCH_BRANCHES_WRITER.insert(db_path=db_path, **kwargs)


def insert_provider_calls(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _PROVIDER_CALLS_WRITER

    _PROVIDER_CALLS_WRITER.insert(db_path=db_path, **kwargs)


def insert_tool_call_event(*, db_path: str | None = None, **kwargs: Any) -> None:
    """Persist one bounded MCP tool lifecycle event asynchronously."""
    from .inserts import _TOOL_CALLS_WRITER

    values = dict(kwargs)
    values.setdefault("event_id", str(uuid4()))
    if isinstance(values.get("payload_json"), (dict, list)):
        values["payload_json"] = json.dumps(values["payload_json"], ensure_ascii=False, default=str)
    _TOOL_CALLS_WRITER.dispatch_insert(db_path=db_path, **values)


def insert_query_understanding_event(*, db_path: str | None = None, **kwargs: Any) -> None:
    """Persist one query-understanding decision asynchronously."""
    from .inserts import _QUERY_UNDERSTANDING_WRITER

    values = dict(kwargs)
    for field in ("scores_json", "entities_json", "payload_json"):
        if isinstance(values.get(field), (dict, list)):
            values[field] = json.dumps(values[field], ensure_ascii=False, default=str)
    _QUERY_UNDERSTANDING_WRITER.dispatch_insert(db_path=db_path, **values)


def insert_search_candidates(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _SEARCH_CANDIDATES_WRITER

    _SEARCH_CANDIDATES_WRITER.insert(db_path=db_path, **kwargs)


def insert_rerank_stages(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _RERANK_STAGES_WRITER

    _RERANK_STAGES_WRITER.insert(db_path=db_path, **kwargs)


def insert_rerank_candidates(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _RERANK_CANDIDATES_WRITER

    _RERANK_CANDIDATES_WRITER.insert(db_path=db_path, **kwargs)


def insert_final_results(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _FINAL_RESULTS_WRITER

    _FINAL_RESULTS_WRITER.insert(db_path=db_path, **kwargs)


def insert_query_embeddings(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _QUERY_EMBEDDINGS_WRITER

    _QUERY_EMBEDDINGS_WRITER.insert(db_path=db_path, **kwargs)


def insert_candidate_embeddings(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _CANDIDATE_EMBEDDINGS_WRITER

    _CANDIDATE_EMBEDDINGS_WRITER.insert(db_path=db_path, **kwargs)


def insert_search_quality_scores(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _SEARCH_QUALITY_SCORES_WRITER

    _SEARCH_QUALITY_SCORES_WRITER.insert(db_path=db_path, **kwargs)


def insert_judge_evaluation(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _JUDGE_EVALUATION_WRITER

    _JUDGE_EVALUATION_WRITER.insert(db_path=db_path, **kwargs)


def insert_llm_call_log(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _LLM_CALL_LOG_WRITER

    _LLM_CALL_LOG_WRITER.dispatch_insert(db_path=db_path, **kwargs)


def insert_ab_experiment(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _AB_EXPERIMENT_WRITER

    _AB_EXPERIMENT_WRITER.insert(db_path=db_path, **kwargs)


def insert_ab_shadow_run(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _AB_SHADOW_RUN_WRITER

    _AB_SHADOW_RUN_WRITER.insert(db_path=db_path, **kwargs)


def insert_search_outcome_batches(
    *,
    search_runs: list[dict[str, Any]],
    search_branches: list[dict[str, Any]],
    provider_calls: list[dict[str, Any]],
    search_candidates: list[dict[str, Any]],
    final_results: list[dict[str, Any]],
    query_embeddings: list[dict[str, Any]],
    candidate_embeddings: list[dict[str, Any]],
    rerank_stages: list[dict[str, Any]],
    db_path: str | None = None,
) -> None:
    """Persist one search outcome with one connection per populated table."""
    from .inserts import (
        _CANDIDATE_EMBEDDINGS_WRITER,
        _FINAL_RESULTS_WRITER,
        _PROVIDER_CALLS_WRITER,
        _QUERY_EMBEDDINGS_WRITER,
        _RERANK_STAGES_WRITER,
        _SEARCH_BRANCHES_WRITER,
        _SEARCH_CANDIDATES_WRITER,
        _SEARCH_RUN_WRITER,
    )

    batches = (
        (_SEARCH_RUN_WRITER, search_runs),
        (_SEARCH_BRANCHES_WRITER, search_branches),
        (_PROVIDER_CALLS_WRITER, provider_calls),
        (_SEARCH_CANDIDATES_WRITER, search_candidates),
        (_FINAL_RESULTS_WRITER, final_results),
        (_QUERY_EMBEDDINGS_WRITER, query_embeddings),
        (_CANDIDATE_EMBEDDINGS_WRITER, candidate_embeddings),
        (_RERANK_STAGES_WRITER, rerank_stages),
    )
    for writer, rows in batches:
        writer.insert_batch(rows, db_path=db_path)


# ---------------------------------------------------------------------------
# Value-extraction helpers (kept for backward compat with any callers)
# ---------------------------------------------------------------------------
def _event_value(payload: dict[str, Any], key: str) -> str | int | float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _provider_value(payload: dict[str, Any]) -> str | None:
    value = payload.get("provider")
    if isinstance(value, str):
        return value
    return None


def _model_used_value(payload: dict[str, Any]) -> str | None:
    value = payload.get("model_used")
    if isinstance(value, str):
        return value
    return None


def _int_value(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _run_key(payload: dict[str, Any]) -> str | None:
    run_key = payload.get("run_key")
    if isinstance(run_key, str):
        return run_key
    tool_call_id = payload.get("tool_call_id")
    if isinstance(tool_call_id, str):
        return tool_call_id
    return None


def _phase(event_name: str) -> str | None:
    parts = event_name.rsplit(".", 1)
    return parts[1] if len(parts) == 2 else None


def _duration_ms_value(payload: dict[str, Any]) -> float | None:
    value = payload.get("duration_ms")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _input_count_value(payload: dict[str, Any]) -> int | None:
    value = _int_value(
        payload,
        ("input_count", "candidates_input", "candidate_count"),
    )
    if value is not None:
        return value
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        return len(candidates)
    return None


def _output_count_value(payload: dict[str, Any]) -> int | None:
    value = _int_value(
        payload,
        ("output_count", "candidates_output", "result_count", "final_count"),
    )
    if value is not None:
        return value
    results = payload.get("results")
    if isinstance(results, list):
        return len(results)
    return None


def _input_tokens_value(payload: dict[str, Any]) -> int | None:
    usage = extract_llm_usage(payload)
    return usage.input_tokens if usage else None


def _output_tokens_value(payload: dict[str, Any]) -> int | None:
    usage = extract_llm_usage(payload)
    return usage.output_tokens if usage else None
