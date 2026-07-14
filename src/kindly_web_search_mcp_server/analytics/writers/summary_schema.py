"""Schema helpers for daily summary analytics tables."""

from __future__ import annotations

import duckdb

from .table_names import (
    _SUM_ID_TABLE_NAME,
    _SUM_PVD_TABLE_NAME,
    _SUM_QD_TABLE_NAME,
    _SUM_RD_TABLE_NAME,
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


def _ensure_summary_provider_daily(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _SUM_PVD_TABLE_NAME,
        [
            ("day", "DATE NOT NULL"),
            ("provider", "VARCHAR NOT NULL"),
            ("query_count", "BIGINT"),
            ("avg_results_returned", "DOUBLE"),
            ("p50_results_returned", "DOUBLE"),
            ("avg_latency_ms", "DOUBLE"),
            ("p50_latency_ms", "DOUBLE"),
            ("p95_latency_ms", "DOUBLE"),
            ("error_rate", "DOUBLE"),
            ("distinct_queries", "BIGINT"),
        ],
        constraints=["PRIMARY KEY (day, provider)"],
    )


def _ensure_summary_intent_daily(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _SUM_ID_TABLE_NAME,
        [
            ("day", "DATE NOT NULL"),
            ("intent", "VARCHAR NOT NULL"),
            ("query_count", "BIGINT"),
            ("avg_confidence", "DOUBLE"),
            ("avg_branch_count", "DOUBLE"),
        ],
        constraints=["PRIMARY KEY (day, intent)"],
    )


def _ensure_summary_rerank_daily(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _SUM_RD_TABLE_NAME,
        [
            ("day", "DATE NOT NULL"),
            ("stage", "VARCHAR NOT NULL"),
            ("provider", "VARCHAR NOT NULL"),
            ("runs_count", "BIGINT"),
            ("avg_compression_ratio", "DOUBLE"),
            ("avg_max_score", "DOUBLE"),
            ("p50_latency_ms", "DOUBLE"),
            ("p95_latency_ms", "DOUBLE"),
            ("entity_overlap_runs", "BIGINT"),
        ],
        constraints=["PRIMARY KEY (day, stage, provider)"],
    )


def _ensure_summary_quality_daily(connection: duckdb.DuckDBPyConnection) -> None:
    _create_table(
        connection,
        _SUM_QD_TABLE_NAME,
        [
            ("day", "DATE NOT NULL"),
            ("avg_overlap_rate", "DOUBLE"),
            ("avg_domain_diversity", "DOUBLE"),
            ("avg_domain_diversity_ratio", "DOUBLE"),
            ("avg_compression_ratio", "DOUBLE"),
            ("avg_top_score", "DOUBLE"),
        ],
        constraints=["PRIMARY KEY (day)"],
    )
