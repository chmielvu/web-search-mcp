"""TableWriter abstraction and public insert wrappers."""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

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


def _serialize_json_fields(values: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    out = dict(values)
    for field in fields:
        val = out.get(field)
        if isinstance(val, (dict, list)):
            out[field] = json.dumps(val, ensure_ascii=False, default=str)
    return out


# ---------------------------------------------------------------------------
# Quick Web Search insert helpers
# ---------------------------------------------------------------------------
def insert_quick_web_search_run(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _QUICK_WEB_SEARCH_RUNS_WRITER

    values = _serialize_json_fields(kwargs, ("warnings", "usage", "payload_json"))
    _QUICK_WEB_SEARCH_RUNS_WRITER.dispatch_insert(db_path=db_path, **values)


def insert_quick_web_search_citations(
    rows: list[dict[str, Any]], *, db_path: str | None = None
) -> None:
    from .inserts import _QUICK_WEB_SEARCH_CITATIONS_WRITER

    serialized = [_serialize_json_fields(r, ("payload_json",)) for r in rows]
    _QUICK_WEB_SEARCH_CITATIONS_WRITER.dispatch_insert_batch(serialized, db_path=db_path)


# ---------------------------------------------------------------------------
# Gemini Search insert helpers
# ---------------------------------------------------------------------------
def insert_gemini_search_run(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _GEMINI_SEARCH_RUNS_WRITER

    values = _serialize_json_fields(kwargs, ("structured_data", "payload_json"))
    _GEMINI_SEARCH_RUNS_WRITER.dispatch_insert(db_path=db_path, **values)


def insert_gemini_search_sources(rows: list[dict[str, Any]], *, db_path: str | None = None) -> None:
    from .inserts import _GEMINI_SEARCH_SOURCES_WRITER

    serialized = [_serialize_json_fields(r, ("source_json",)) for r in rows]
    _GEMINI_SEARCH_SOURCES_WRITER.dispatch_insert_batch(serialized, db_path=db_path)


def insert_gemini_search_attempts(
    rows: list[dict[str, Any]], *, db_path: str | None = None
) -> None:
    from .inserts import _GEMINI_SEARCH_ATTEMPTS_WRITER

    serialized = [_serialize_json_fields(r, ("payload_json",)) for r in rows]
    _GEMINI_SEARCH_ATTEMPTS_WRITER.dispatch_insert_batch(serialized, db_path=db_path)


# ---------------------------------------------------------------------------
# Code Search insert helpers
# ---------------------------------------------------------------------------
def insert_code_search_run(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _CODE_SEARCH_RUNS_WRITER

    values = _serialize_json_fields(
        kwargs,
        ("planner_source_tokens", "planner_qualifiers", "provider_hit_counts", "payload_json"),
    )
    _CODE_SEARCH_RUNS_WRITER.dispatch_insert(db_path=db_path, **values)


def insert_code_search_providers(rows: list[dict[str, Any]], *, db_path: str | None = None) -> None:
    from .inserts import _CODE_SEARCH_PROVIDERS_WRITER

    serialized = [_serialize_json_fields(r, ("payload_json",)) for r in rows]
    _CODE_SEARCH_PROVIDERS_WRITER.dispatch_insert_batch(serialized, db_path=db_path)


def insert_code_search_diagnostics(
    rows: list[dict[str, Any]], *, db_path: str | None = None
) -> None:
    from .inserts import _CODE_SEARCH_DIAGNOSTICS_WRITER

    serialized = [_serialize_json_fields(r, ("details",)) for r in rows]
    _CODE_SEARCH_DIAGNOSTICS_WRITER.dispatch_insert_batch(serialized, db_path=db_path)


def insert_code_search_hits(rows: list[dict[str, Any]], *, db_path: str | None = None) -> None:
    from .inserts import _CODE_SEARCH_HITS_WRITER

    serialized = [
        _serialize_json_fields(r, ("score_components", "source_metadata", "payload_json"))
        for r in rows
    ]
    _CODE_SEARCH_HITS_WRITER.dispatch_insert_batch(serialized, db_path=db_path)


def insert_code_search_hit_variants(
    rows: list[dict[str, Any]], *, db_path: str | None = None
) -> None:
    from .inserts import _CODE_SEARCH_HIT_VARIANTS_WRITER

    _CODE_SEARCH_HIT_VARIANTS_WRITER.dispatch_insert_batch(rows, db_path=db_path)


def insert_code_search_query_variants(
    rows: list[dict[str, Any]], *, db_path: str | None = None
) -> None:
    from .inserts import _CODE_SEARCH_QUERY_VARIANTS_WRITER

    _CODE_SEARCH_QUERY_VARIANTS_WRITER.dispatch_insert_batch(rows, db_path=db_path)


def insert_code_search_repositories(
    rows: list[dict[str, Any]], *, db_path: str | None = None
) -> None:
    from .inserts import _CODE_SEARCH_REPOSITORIES_WRITER

    serialized = [_serialize_json_fields(r, ("payload_json",)) for r in rows]
    _CODE_SEARCH_REPOSITORIES_WRITER.dispatch_insert_batch(serialized, db_path=db_path)


def insert_code_search_rerank(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _CODE_SEARCH_RERANK_WRITER

    values = _serialize_json_fields(kwargs, ("payload_json",))
    _CODE_SEARCH_RERANK_WRITER.dispatch_insert(db_path=db_path, **values)


# ---------------------------------------------------------------------------
# Content Operations and Summary insert helpers
# ---------------------------------------------------------------------------
def insert_content_operation(*, db_path: str | None = None, **kwargs: Any) -> None:
    from .inserts import _CONTENT_OPERATIONS_WRITER

    values = _serialize_json_fields(kwargs, ("payload_json",))
    _CONTENT_OPERATIONS_WRITER.dispatch_insert(db_path=db_path, **values)


def insert_content_fetches(rows: list[dict[str, Any]], *, db_path: str | None = None) -> None:
    from .inserts import _CONTENT_FETCHES_WRITER

    serialized = [_serialize_json_fields(r, ("payload_json",)) for r in rows]
    _CONTENT_FETCHES_WRITER.dispatch_insert_batch(serialized, db_path=db_path)


def insert_content_summaries(rows: list[dict[str, Any]], *, db_path: str | None = None) -> None:
    from .inserts import _CONTENT_SUMMARIES_WRITER

    serialized = [_serialize_json_fields(r, ("payload_json",)) for r in rows]
    _CONTENT_SUMMARIES_WRITER.dispatch_insert_batch(serialized, db_path=db_path)


def insert_content_summary_attempts(
    rows: list[dict[str, Any]], *, db_path: str | None = None
) -> None:
    from .inserts import _CONTENT_SUMMARY_ATTEMPTS_WRITER

    serialized = [_serialize_json_fields(r, ("payload_json",)) for r in rows]
    _CONTENT_SUMMARY_ATTEMPTS_WRITER.dispatch_insert_batch(serialized, db_path=db_path)


def insert_quick_web_search_batches(
    *,
    quick_web_search_runs: list[dict[str, Any]] | None = None,
    quick_web_search_citations: list[dict[str, Any]] | None = None,
    db_path: str | None = None,
) -> None:
    """Persist one quick search outcome synchronously across child tables."""
    from .inserts import _QUICK_WEB_SEARCH_CITATIONS_WRITER, _QUICK_WEB_SEARCH_RUNS_WRITER

    if quick_web_search_runs:
        serialized_runs = [
            _serialize_json_fields(r, ("warnings", "usage", "payload_json"))
            for r in quick_web_search_runs
        ]
        _QUICK_WEB_SEARCH_RUNS_WRITER.insert_batch(serialized_runs, db_path=db_path)
    if quick_web_search_citations:
        serialized_citations = [
            _serialize_json_fields(r, ("payload_json",)) for r in quick_web_search_citations
        ]
        _QUICK_WEB_SEARCH_CITATIONS_WRITER.insert_batch(serialized_citations, db_path=db_path)


def insert_gemini_search_batches(
    *,
    gemini_search_runs: list[dict[str, Any]] | None = None,
    gemini_search_sources: list[dict[str, Any]] | None = None,
    gemini_search_attempts: list[dict[str, Any]] | None = None,
    db_path: str | None = None,
) -> None:
    """Persist one Gemini search outcome synchronously across child tables."""
    from .inserts import (
        _GEMINI_SEARCH_ATTEMPTS_WRITER,
        _GEMINI_SEARCH_RUNS_WRITER,
        _GEMINI_SEARCH_SOURCES_WRITER,
    )

    if gemini_search_runs:
        serialized_runs = [
            _serialize_json_fields(r, ("structured_data", "payload_json"))
            for r in gemini_search_runs
        ]
        _GEMINI_SEARCH_RUNS_WRITER.insert_batch(serialized_runs, db_path=db_path)
    if gemini_search_sources:
        serialized_sources = [
            _serialize_json_fields(r, ("source_json",)) for r in gemini_search_sources
        ]
        _GEMINI_SEARCH_SOURCES_WRITER.insert_batch(serialized_sources, db_path=db_path)
    if gemini_search_attempts:
        serialized_attempts = [
            _serialize_json_fields(r, ("payload_json",)) for r in gemini_search_attempts
        ]
        _GEMINI_SEARCH_ATTEMPTS_WRITER.insert_batch(serialized_attempts, db_path=db_path)


def insert_code_search_batches(
    *,
    code_search_runs: list[dict[str, Any]] | None = None,
    code_search_providers: list[dict[str, Any]] | None = None,
    code_search_diagnostics: list[dict[str, Any]] | None = None,
    code_search_hits: list[dict[str, Any]] | None = None,
    code_search_hit_variants: list[dict[str, Any]] | None = None,
    code_search_query_variants: list[dict[str, Any]] | None = None,
    code_search_repositories: list[dict[str, Any]] | None = None,
    code_search_rerank: list[dict[str, Any]] | None = None,
    db_path: str | None = None,
) -> None:
    """Persist one code search outcome synchronously across child tables."""
    from .inserts import (
        _CODE_SEARCH_DIAGNOSTICS_WRITER,
        _CODE_SEARCH_HITS_WRITER,
        _CODE_SEARCH_HIT_VARIANTS_WRITER,
        _CODE_SEARCH_PROVIDERS_WRITER,
        _CODE_SEARCH_QUERY_VARIANTS_WRITER,
        _CODE_SEARCH_REPOSITORIES_WRITER,
        _CODE_SEARCH_RERANK_WRITER,
        _CODE_SEARCH_RUNS_WRITER,
    )

    if code_search_runs:
        serialized_runs = [
            _serialize_json_fields(
                r,
                (
                    "planner_source_tokens",
                    "planner_qualifiers",
                    "provider_hit_counts",
                    "payload_json",
                ),
            )
            for r in code_search_runs
        ]
        _CODE_SEARCH_RUNS_WRITER.insert_batch(serialized_runs, db_path=db_path)
    if code_search_providers:
        serialized_providers = [
            _serialize_json_fields(r, ("payload_json",)) for r in code_search_providers
        ]
        _CODE_SEARCH_PROVIDERS_WRITER.insert_batch(serialized_providers, db_path=db_path)
    if code_search_diagnostics:
        serialized_diags = [
            _serialize_json_fields(r, ("details",)) for r in code_search_diagnostics
        ]
        _CODE_SEARCH_DIAGNOSTICS_WRITER.insert_batch(serialized_diags, db_path=db_path)
    if code_search_hits:
        serialized_hits = [
            _serialize_json_fields(r, ("score_components", "source_metadata", "payload_json"))
            for r in code_search_hits
        ]
        _CODE_SEARCH_HITS_WRITER.insert_batch(serialized_hits, db_path=db_path)
    if code_search_hit_variants:
        _CODE_SEARCH_HIT_VARIANTS_WRITER.insert_batch(code_search_hit_variants, db_path=db_path)
    if code_search_query_variants:
        _CODE_SEARCH_QUERY_VARIANTS_WRITER.insert_batch(code_search_query_variants, db_path=db_path)
    if code_search_repositories:
        serialized_repos = [
            _serialize_json_fields(r, ("payload_json",)) for r in code_search_repositories
        ]
        _CODE_SEARCH_REPOSITORIES_WRITER.insert_batch(serialized_repos, db_path=db_path)
    if code_search_rerank:
        serialized_rerank = [
            _serialize_json_fields(r, ("payload_json",)) for r in code_search_rerank
        ]
        _CODE_SEARCH_RERANK_WRITER.insert_batch(serialized_rerank, db_path=db_path)


def insert_content_operation_batches(
    *,
    content_operations: list[dict[str, Any]] | None = None,
    content_fetches: list[dict[str, Any]] | None = None,
    content_summaries: list[dict[str, Any]] | None = None,
    content_summary_attempts: list[dict[str, Any]] | None = None,
    db_path: str | None = None,
) -> None:
    """Persist one content operation outcome synchronously across child tables."""
    from .inserts import (
        _CONTENT_FETCHES_WRITER,
        _CONTENT_OPERATIONS_WRITER,
        _CONTENT_SUMMARIES_WRITER,
        _CONTENT_SUMMARY_ATTEMPTS_WRITER,
    )

    if content_operations:
        serialized_ops = [_serialize_json_fields(r, ("payload_json",)) for r in content_operations]
        _CONTENT_OPERATIONS_WRITER.insert_batch(serialized_ops, db_path=db_path)
    if content_fetches:
        serialized_fetches = [_serialize_json_fields(r, ("payload_json",)) for r in content_fetches]
        _CONTENT_FETCHES_WRITER.insert_batch(serialized_fetches, db_path=db_path)
    if content_summaries:
        serialized_summaries = [
            _serialize_json_fields(r, ("payload_json",)) for r in content_summaries
        ]
        _CONTENT_SUMMARIES_WRITER.insert_batch(serialized_summaries, db_path=db_path)
    if content_summary_attempts:
        serialized_attempts = [
            _serialize_json_fields(r, ("payload_json",)) for r in content_summary_attempts
        ]
        _CONTENT_SUMMARY_ATTEMPTS_WRITER.insert_batch(serialized_attempts, db_path=db_path)


def insert_funnel_uplift_batches(
    *,
    result_catalog: list[dict[str, Any]] | None = None,
    provider_results: list[dict[str, Any]] | None = None,
    query_variants: list[dict[str, Any]] | None = None,
    query_transforms: list[dict[str, Any]] | None = None,
    candidate_stage_events: list[dict[str, Any]] | None = None,
    tool_output_items: list[dict[str, Any]] | None = None,
    db_path: str | None = None,
) -> None:
    """Persist web-search funnel uplift facts synchronously."""
    from .inserts import (
        _CANDIDATE_STAGE_EVENTS_WRITER,
        _PROVIDER_RESULTS_WRITER,
        _QUERY_VARIANTS_WRITER,
        _QUERY_TRANSFORMS_WRITER,
        _RESULT_CATALOG_WRITER,
        _TOOL_OUTPUT_ITEMS_WRITER,
    )

    recorded_at = datetime.now(timezone.utc)

    def _with_recorded_at(row: dict[str, Any]) -> dict[str, Any]:
        return {
            **row,
            "recorded_at": row.get("recorded_at") or recorded_at,
            "first_seen_at": row.get("first_seen_at") or recorded_at,
        }

    if result_catalog:
        _RESULT_CATALOG_WRITER.insert_batch(
            [_with_recorded_at(row) for row in result_catalog], db_path=db_path
        )
    if provider_results:
        serialized_pr = [
            _with_recorded_at(_serialize_json_fields(row, ("payload_json",)))
            for row in provider_results
        ]
        _PROVIDER_RESULTS_WRITER.insert_batch(serialized_pr, db_path=db_path)
    if query_variants:
        _QUERY_VARIANTS_WRITER.insert_batch(
            [_with_recorded_at(row) for row in query_variants], db_path=db_path
        )
    if query_transforms:
        serialized_qt = [
            _with_recorded_at(_serialize_json_fields(row, ("metadata_json",)))
            for row in query_transforms
        ]
        _QUERY_TRANSFORMS_WRITER.insert_batch(serialized_qt, db_path=db_path)
    if candidate_stage_events:
        serialized_cse = [
            _with_recorded_at(_serialize_json_fields(r, ("payload_json",)))
            for r in candidate_stage_events
        ]
        _CANDIDATE_STAGE_EVENTS_WRITER.insert_batch(serialized_cse, db_path=db_path)
    if tool_output_items:
        _TOOL_OUTPUT_ITEMS_WRITER.insert_batch(
            [_with_recorded_at(row) for row in tool_output_items],
            db_path=db_path,
        )


# ---------------------------------------------------------------------------
# Result Labels foundation insert helpers
# ---------------------------------------------------------------------------
def _generate_result_label_id(
    run_key: str,
    position: int,
    stage: str,
    source: str,
    annotator_id: str | None,
    rubric_version: str,
    canonical_or_url: str | None,
) -> str:
    """Deterministic 16-hex hash for idempotent result_label insertion."""
    key_dict = {
        "run_key": str(run_key),
        "position": int(position),
        "stage": str(stage),
        "source": str(source),
        "annotator_id": str(annotator_id or ""),
        "rubric_version": str(rubric_version),
        "target": str(canonical_or_url or ""),
    }
    raw = json.dumps(key_dict, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return sha256(raw).hexdigest()[:16]


def _prepare_result_label_row(values: dict[str, Any]) -> dict[str, Any]:
    """Validate, normalize, and populate stable IDs/discounts for a result_labels row."""
    from ..quality_metrics import compute_positional_discount

    if not isinstance(values, dict):
        raise TypeError(f"Expected dict for result label row, got {type(values).__name__}")

    run_key = values.get("run_key")
    if not run_key or not isinstance(run_key, str) or not run_key.strip():
        raise ValueError("run_key must be a non-empty string")
    run_key = run_key.strip()

    position = values.get("position")
    if position is None or not isinstance(position, int) or isinstance(position, bool):
        raise TypeError(f"position must be an integer >= 0, got {position!r}")
    if position < 0:
        raise ValueError(f"position must be >= 0, got {position}")

    raw_label = values.get("label")
    if raw_label is None:
        raise ValueError("label must be provided")
    try:
        label = float(raw_label)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"label must be a real number, got {raw_label!r}") from exc
    if math.isnan(label) or math.isinf(label) or label < 0.0:
        raise ValueError(f"label must be a nonnegative finite number, got {raw_label!r}")
    stage = str(values.get("stage") or "final").strip()
    if not stage:
        raise ValueError("stage must be a non-empty string")

    source = str(values.get("source") or "human").strip()
    if not source:
        raise ValueError("source must be a non-empty string")
    annotator_id = values.get("annotator_id")
    if annotator_id is not None:
        annotator_id = str(annotator_id).strip() or None
    rubric_version = str(values.get("rubric_version") or "v1").strip()
    if not rubric_version:
        raise ValueError("rubric_version must be a non-empty string")

    canonical_result_id = values.get("canonical_result_id")
    if canonical_result_id is not None:
        canonical_result_id = str(canonical_result_id).strip()

    raw_url = values.get("raw_url") or values.get("link") or values.get("url")
    if raw_url is not None:
        raw_url = str(raw_url).strip()

    # Auto-derive canonical_result_id from link/URL if omitted and URL present
    if not canonical_result_id and raw_url:
        canonical_result_id = sha256(
            json.dumps({"link": raw_url.lower()}, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

    label_id = values.get("label_id")
    if not label_id or not isinstance(label_id, str) or not label_id.strip():
        label_id = _generate_result_label_id(
            run_key,
            position,
            stage,
            source,
            annotator_id,
            rubric_version,
            canonical_result_id or raw_url,
        )
    else:
        label_id = str(label_id).strip()

    discounted_gain = values.get("discounted_gain")
    if discounted_gain is None:
        discounted_gain = compute_positional_discount(label, position)
    else:
        discounted_gain = float(discounted_gain)
        if math.isnan(discounted_gain) or math.isinf(discounted_gain) or discounted_gain < 0.0:
            raise ValueError(
                f"discounted_gain must be a nonnegative finite number, got {discounted_gain!r}"
            )

    notes = values.get("notes")
    if notes is not None:
        notes = str(notes)

    payload_json = values.get("payload_json")
    if isinstance(payload_json, (dict, list)):
        payload_json = json.dumps(payload_json, ensure_ascii=False, default=str)

    recorded_at = values.get("recorded_at") or datetime.now(timezone.utc)

    return {
        "label_id": label_id,
        "recorded_at": recorded_at,
        "run_key": run_key,
        "stage": stage,
        "position": position,
        "label": label,
        "canonical_result_id": canonical_result_id,
        "raw_url": raw_url,
        "source": source,
        "annotator_id": annotator_id,
        "rubric_version": rubric_version,
        "discounted_gain": discounted_gain,
        "notes": notes,
        "payload_json": payload_json,
    }


def insert_result_label(
    *,
    db_path: str | None = None,
    sync: bool = False,
    **kwargs: Any,
) -> None:
    """Persist a single result label / annotation (asynchronously by default)."""
    from .inserts import _RESULT_LABELS_WRITER

    row = _prepare_result_label_row(kwargs)
    if sync:
        _RESULT_LABELS_WRITER.insert(db_path=db_path, **row)
    else:
        _RESULT_LABELS_WRITER.dispatch_insert(db_path=db_path, **row)


def insert_result_labels(
    rows: list[dict[str, Any]],
    *,
    db_path: str | None = None,
    sync: bool = False,
) -> None:
    """Persist multiple result labels / annotations (asynchronously by default)."""
    if not rows:
        return
    from .inserts import _RESULT_LABELS_WRITER

    prepared = [_prepare_result_label_row(r) for r in rows]
    if sync:
        _RESULT_LABELS_WRITER.insert_batch(prepared, db_path=db_path)
    else:
        _RESULT_LABELS_WRITER.dispatch_insert_batch(prepared, db_path=db_path)


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
