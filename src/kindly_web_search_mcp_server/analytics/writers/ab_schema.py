"""Schema helpers for A/B testing analytics tables."""

from __future__ import annotations

import duckdb

from .table_names import (
    _ABA_TABLE_NAME,
    _ABE_TABLE_NAME,
    _ABR_TABLE_NAME,
    _ABS_TABLE_NAME,
    _ABV_TABLE_NAME,
)


def _create_table(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    columns: list[tuple[str, str]],
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


def _ensure_ab_experiments(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _ABE_TABLE_NAME,
        [
            ("experiment_id", "VARCHAR NOT NULL PRIMARY KEY"),
            ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("layer", "VARCHAR NOT NULL"),
            ("variant_a", "VARCHAR NOT NULL"),
            ("variant_b", "VARCHAR NOT NULL"),
            ("allocation_rate", "DOUBLE NOT NULL DEFAULT 0.5"),
            ("status", "VARCHAR NOT NULL DEFAULT 'active'"),
            ("start_date", "DATE"),
            ("end_date", "DATE"),
            ("min_sample_size", "INTEGER"),
            ("payload_json", "JSON"),
        ],
    )


def _ensure_ab_shadow_runs(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _ABS_TABLE_NAME,
        [
            ("run_key", "VARCHAR NOT NULL"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("experiment_id", "VARCHAR NOT NULL"),
            ("variant", "VARCHAR NOT NULL"),
            ("layer", "VARCHAR NOT NULL"),
            ("duration_ms", "DOUBLE"),
            ("judge_score", "DOUBLE"),
            ("tokens_used", "INTEGER"),
            ("cost_usd", "DOUBLE"),
            ("error_type", "VARCHAR"),
            ("payload_json", "JSON"),
        ],
    )


def _ensure_ab_experiment_variants(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _ABV_TABLE_NAME,
        [
            ("variant_id", "VARCHAR NOT NULL PRIMARY KEY"),
            ("experiment_id", "VARCHAR NOT NULL"),
            ("variant_name", "VARCHAR NOT NULL"),
            ("description", "VARCHAR"),
            ("config_json", "JSON"),
            ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ],
    )


def _ensure_ab_assignments(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _ABA_TABLE_NAME,
        [
            ("assignment_id", "VARCHAR NOT NULL PRIMARY KEY"),
            ("experiment_id", "VARCHAR NOT NULL"),
            ("run_key", "VARCHAR NOT NULL"),
            ("variant", "VARCHAR NOT NULL"),
            ("assigned_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("payload_json", "JSON"),
        ],
    )


def _ensure_ab_results(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _ABR_TABLE_NAME,
        [
            ("result_id", "VARCHAR NOT NULL PRIMARY KEY"),
            ("experiment_id", "VARCHAR NOT NULL"),
            ("run_key", "VARCHAR NOT NULL"),
            ("variant", "VARCHAR NOT NULL"),
            ("primary_metric", "DOUBLE"),
            ("secondary_metric", "DOUBLE"),
            ("duration_ms", "DOUBLE"),
            ("recorded_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
            ("payload_json", "JSON"),
        ],
    )
