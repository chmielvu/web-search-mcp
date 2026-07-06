"""CREATE TABLE / _ensure_* schema bootstrap for analytics writers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb

from .connection import _db_path, _ensure_columns, _LOCK
from .migrations import apply_search_events_migrations
from .table_names import (
    _FR_TABLE_NAME,
    _JE_TABLE_NAME,
    _MC_TABLE_NAME,
    _PC_TABLE_NAME,
    _PRC_TABLE_NAME,
    _QR_TABLE_NAME,
    _QU_TABLE_NAME,
    _RC_TABLE_NAME,
    _RS_TABLE_NAME,
    _RUNS_TABLE_NAME,
    _SQS_TABLE_NAME,
    _TABLE_NAME,
)

if TYPE_CHECKING:
    pass


def _create_table(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    columns: list[tuple[str, str]],
    indexes: list[tuple[str, str]] | None = None,
    constraints: list[str] | None = None,
) -> None:
    col_defs = [f"{name} {type_}" for name, type_ in columns]
    if constraints:
        col_defs.extend(constraints)
    col_block = ",\n            ".join(col_defs)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            {col_block}
        )
        """
    )
    if indexes:
        for index_name, index_columns in indexes:
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}({index_columns})"
            )


def _ensure_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``search_events`` (with backfill migrations) if absent."""
    _create_table(
        connection,
        _TABLE_NAME,
        [
            ("event_id", "VARCHAR"),
            ("event_name", "VARCHAR"),
            ("recorded_at", "TIMESTAMP"),
            ("run_key", "VARCHAR"),
            ("tool_name", "VARCHAR"),
            ("phase", "VARCHAR"),
            ("query", "VARCHAR"),
            ("normalized_query", "VARCHAR"),
            ("research_goal", "VARCHAR"),
            ("provider", "VARCHAR"),
            ("model", "VARCHAR"),
            ("model_used", "VARCHAR"),
            ("duration_ms", "DOUBLE"),
            ("input_count", "INTEGER"),
            ("output_count", "INTEGER"),
            ("input_tokens", "INTEGER"),
            ("output_tokens", "INTEGER"),
            ("trace_id", "VARCHAR"),
            ("span_id", "VARCHAR"),
            ("cache_hit", "VARCHAR"),
            ("payload_json", "VARCHAR"),
        ],
    )
    _ensure_columns(
        connection,
        _TABLE_NAME,
        {
            "event_id": "VARCHAR",
            "run_key": "VARCHAR",
            "tool_name": "VARCHAR",
            "phase": "VARCHAR",
            "cache_hit": "VARCHAR",
            "model_used": "VARCHAR",
            "input_tokens": "INTEGER",
            "output_tokens": "INTEGER",
        },
    )
    apply_search_events_migrations(connection)


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


def ensure_search_quality_tables(*, db_path: str | None = None) -> None:
    """Ensure the tables needed by quality scoring and judge writes exist.

    This is a light bootstrap/migration pass for fresh or legacy DuckDB files.
    It creates the pipeline tables that quality metrics query before the score
    row is written, plus the judge table used by the background judge writer.
    """
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_schema(connection)
            _ensure_search_runs(connection)
            _ensure_provider_calls(connection)
            _ensure_provider_candidates(connection)
            _ensure_merged_candidates(connection)
            _ensure_rerank_stages(connection)
            _ensure_rerank_candidates(connection)
            _ensure_final_results(connection)
            _ensure_query_rewrites(connection)
            _ensure_search_quality_scores(connection)
            _ensure_judge_evaluations(connection)
        finally:
            connection.close()


def _ensure_search_runs(connection: duckdb.DuckDBPyConnection) -> None:
    """Create search_runs table with indexes if it doesn't exist."""
    _create_table(
        connection,
        _RUNS_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("query", "VARCHAR NOT NULL"),
            ("normalized_query", "VARCHAR"),
            ("research_goal", "VARCHAR"),
            ("num_results_requested", "INTEGER"),
            ("rewrite_enabled", "BOOLEAN"),
            ("session_id", "VARCHAR"),
            ("tool_name", "VARCHAR DEFAULT 'web_search'"),
            ("duration_ms", "DOUBLE"),
            ("final_result_count", "INTEGER"),
            ("candidate_count", "INTEGER"),
            ("has_more", "BOOLEAN"),
            ("result_offset", "INTEGER"),
            ("status", "VARCHAR"),
            ("error_type", "VARCHAR"),
            ("reranker_provider", "VARCHAR"),
            ("reranker_model", "VARCHAR"),
            ("payload_json", "JSON"),
        ],
        indexes=[
            ("idx_runs_run_key", "run_key"),
            ("idx_runs_recorded_at", "recorded_at"),
        ],
    )
    _ensure_columns(
        connection,
        _RUNS_TABLE_NAME,
        {
            "reranker_provider": "VARCHAR",
            "reranker_model": "VARCHAR",
        },
    )


def _ensure_query_understanding(connection: duckdb.DuckDBPyConnection) -> None:
    """Create query_understanding table with index if it doesn't exist."""
    _create_table(
        connection,
        _QU_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("intent", "VARCHAR"),
            ("confidence", "DOUBLE"),
            ("should_decompose", "BOOLEAN"),
            ("rationale", "VARCHAR"),
            ("model", "VARCHAR"),
            ("model_used", "VARCHAR"),
            ("provider", "VARCHAR"),
            ("duration_ms", "DOUBLE"),
            ("fallback_used", "BOOLEAN"),
            ("entities_count", "INTEGER"),
            ("input_tokens", "INTEGER"),
            ("output_tokens", "INTEGER"),
            ("preserved_terms", "VARCHAR[]"),
            ("time_sensitivity", "VARCHAR"),
            ("payload_json", "JSON"),
        ],
    )
    _ensure_columns(
        connection,
        _QU_TABLE_NAME,
        {
            "model_used": "VARCHAR",
            "input_tokens": "INTEGER",
            "output_tokens": "INTEGER",
        },
    )


def _ensure_query_rewrites(connection: duckdb.DuckDBPyConnection) -> None:
    """Create query_rewrites table with index if it doesn't exist."""
    _create_table(
        connection,
        _QR_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("variant_index", "INTEGER"),
            ("branch_type", "VARCHAR"),
            ("kind", "VARCHAR"),
            ("target", "VARCHAR"),
            ("query", "VARCHAR NOT NULL"),
            ("weight", "DOUBLE"),
            ("reason", "VARCHAR"),
            ("max_results", "INTEGER"),
            ("model", "VARCHAR"),
            ("model_used", "VARCHAR"),
            ("duration_ms", "DOUBLE"),
            ("input_tokens", "INTEGER"),
            ("output_tokens", "INTEGER"),
            ("payload_json", "JSON"),
        ],
    )
    _ensure_columns(
        connection,
        _QR_TABLE_NAME,
        {
            "model_used": "VARCHAR",
            "input_tokens": "INTEGER",
            "output_tokens": "INTEGER",
        },
    )


def _ensure_provider_calls(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _PC_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("provider", "VARCHAR NOT NULL"),
            ("branch_index", "INTEGER"),
            ("branch_query", "VARCHAR"),
            ("num_results_requested", "INTEGER"),
            ("num_results_returned", "INTEGER"),
            ("duration_ms", "DOUBLE"),
            ("error_code", "VARCHAR"),
            ("error_message", "VARCHAR"),
            ("http_status", "INTEGER"),
            ("tokens_used", "INTEGER"),
            ("cost_usd", "DOUBLE"),
            ("payload_json", "JSON"),
        ],
    )


def _ensure_provider_candidates(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _PRC_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("provider", "VARCHAR NOT NULL"),
            ("branch_index", "INTEGER"),
            ("rank", "INTEGER"),
            ("title", "VARCHAR"),
            ("link", "VARCHAR"),
            ("snippet", "VARCHAR"),
            ("domain", "VARCHAR"),
            ("score", "DOUBLE"),
            ("published_date", "VARCHAR"),
            ("payload_json", "JSON"),
        ],
    )


def _ensure_merged_candidates(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _MC_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("rank", "INTEGER"),
            ("title", "VARCHAR"),
            ("link", "VARCHAR"),
            ("snippet", "VARCHAR"),
            ("domain", "VARCHAR"),
            ("rrf_score", "DOUBLE"),
            ("provider_count", "INTEGER"),
            ("providers", "VARCHAR[]"),
            ("overlap_flag", "BOOLEAN"),
            ("payload_json", "JSON"),
        ],
    )


def _ensure_rerank_stages(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _RS_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("stage", "VARCHAR NOT NULL"),
            ("provider", "VARCHAR"),
            ("model", "VARCHAR"),
            ("model_used", "VARCHAR"),
            ("input_count", "INTEGER"),
            ("output_count", "INTEGER"),
            ("input_tokens", "INTEGER"),
            ("output_tokens", "INTEGER"),
            ("duration_ms", "DOUBLE"),
            ("max_score", "DOUBLE"),
            ("avg_score", "DOUBLE"),
            ("score_threshold", "DOUBLE"),
            ("instruction_present", "BOOLEAN"),
            ("instruction_length", "INTEGER"),
            ("query_type_hint", "VARCHAR"),
            ("entity_overlap_enabled", "BOOLEAN"),
            ("payload_json", "JSON"),
        ],
    )
    _ensure_columns(
        connection,
        _RS_TABLE_NAME,
        {
            "model_used": "VARCHAR",
            "input_tokens": "INTEGER",
            "output_tokens": "INTEGER",
        },
    )


def _ensure_rerank_candidates(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _RC_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("stage", "VARCHAR NOT NULL"),
            ("link", "VARCHAR NOT NULL"),
            ("rank_before", "INTEGER"),
            ("rank_after", "INTEGER"),
            ("score_before", "DOUBLE"),
            ("score_after", "DOUBLE"),
            ("score_after_relevance", "DOUBLE"),
            ("score_after_recency", "DOUBLE"),
            ("score_after_entity", "DOUBLE"),
            ("recency_boost", "DOUBLE"),
            ("entity_overlap_score", "DOUBLE"),
            ("diversity_removed", "BOOLEAN"),
            ("payload_json", "JSON"),
        ],
    )


def _ensure_final_results(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _FR_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("rank", "INTEGER"),
            ("title", "VARCHAR"),
            ("link", "VARCHAR"),
            ("snippet", "VARCHAR"),
            ("domain", "VARCHAR"),
            ("final_score", "DOUBLE"),
            ("providers", "VARCHAR[]"),
            ("provider_count", "INTEGER"),
            ("entities_count", "INTEGER"),
            ("payload_json", "JSON"),
        ],
    )


def _ensure_search_quality_scores(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _SQS_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL PRIMARY KEY"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("provider_overlap_rate", "DOUBLE"),
            ("domain_diversity_count", "INTEGER"),
            ("domain_diversity_ratio", "DOUBLE"),
            ("rerank_compression_ratio", "DOUBLE"),
            ("avg_rrf_score", "DOUBLE"),
            ("top_score", "DOUBLE"),
            ("p95_score", "DOUBLE"),
            ("rewrite_variant_count", "INTEGER"),
            ("provider_count", "INTEGER"),
            ("branch_count", "INTEGER"),
            ("total_candidates_input", "INTEGER"),
            ("total_candidates_merged", "INTEGER"),
            ("total_candidates_reranked", "INTEGER"),
            ("total_final_results", "INTEGER"),
            ("payload_json", "JSON"),
        ],
    )


def _ensure_judge_evaluations(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _JE_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("tool_name", "VARCHAR"),
            ("judge_model", "VARCHAR"),
            ("model_used", "VARCHAR"),
            ("relevance_score", "DOUBLE"),
            ("relevance_raw", "INTEGER"),
            ("relevance_scale", "VARCHAR"),
            ("accuracy_score", "DOUBLE"),
            ("completeness_score", "DOUBLE"),
            ("source_quality_score", "DOUBLE"),
            ("overall_score", "DOUBLE"),
            ("rationale", "VARCHAR"),
            ("duration_ms", "DOUBLE"),
            ("input_tokens", "INTEGER"),
            ("output_tokens", "INTEGER"),
            ("tokens_used", "INTEGER"),
            ("cost_usd", "DOUBLE"),
            ("payload_json", "JSON"),
        ],
    )
    _ensure_columns(
        connection,
        _JE_TABLE_NAME,
        {
            "model_used": "VARCHAR",
            "input_tokens": "INTEGER",
            "output_tokens": "INTEGER",
            "tokens_used": "INTEGER",
        },
    )


from .ab_schema import (  # noqa: E402
    _ensure_ab_assignments,  # noqa: F401
    _ensure_ab_experiment_variants,  # noqa: F401
    _ensure_ab_experiments,  # noqa: F401
    _ensure_ab_results,  # noqa: F401
    _ensure_ab_shadow_runs,  # noqa: F401
)
from .summary_schema import (  # noqa: E402
    _ensure_summary_intent_daily,  # noqa: F401
    _ensure_summary_provider_daily,  # noqa: F401
    _ensure_summary_quality_daily,  # noqa: F401
    _ensure_summary_rerank_daily,  # noqa: F401
)
