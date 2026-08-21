from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from ...analytics.duckdb_store import _db_path
from ...analytics.formatting import json_safe_rows


def _connect(db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    path = Path(_db_path(db_path))
    if not path.exists():
        raise FileNotFoundError(f"Analytics database not found: {path}")
    return duckdb.connect(str(path), read_only=True)


def _rows(connection: duckdb.DuckDBPyConnection, sql: str, *params: Any) -> list[dict[str, Any]]:
    result = connection.execute(sql, list(params))
    columns = [item[0] for item in result.description]
    return json_safe_rows([dict(zip(columns, row, strict=False)) for row in result.fetchall()])


def inspect_search_run(run_key: str, *, db_path: str | None = None) -> dict[str, Any]:
    connection = _connect(db_path)
    try:
        runs = _rows(
            connection,
            """
            SELECT CAST(recorded_at AS VARCHAR) AS recorded_at,
                   run_key, query, research_goal, intent, status, error_type,
                   duration_ms, provider_count, final_result_count,
                   selected_providers, skipped_providers
            FROM search_runs
            WHERE run_key = ?
            LIMIT 1
            """,
            run_key,
        )
        if not runs:
            raise LookupError(f"Search run '{run_key}' was not found.")
        return {
            "run_key": run_key,
            "run": runs[0],
            "branches": _rows(
                connection,
                """
                SELECT branch_index, branch_role, branch_query, branch_why,
                       assigned_providers, attempted_providers, skipped_providers,
                       results_count, latency_ms
                FROM search_branches
                WHERE run_key = ?
                ORDER BY branch_index
                """,
                run_key,
            ),
            "provider_calls": _rows(
                connection,
                """
                SELECT branch_index, branch_role, provider, status,
                       num_results_returned, latency_ms, error_type,
                       http_status, result_class, retry_after_seconds, retryable
                FROM provider_calls
                WHERE run_key = ?
                ORDER BY recorded_at, branch_index, provider
                """,
                run_key,
            ),
            "rerank_stages": _rows(
                connection,
                """
                SELECT stage, provider, model, input_count, output_count,
                       duration_ms, status, error_type
                FROM rerank_stages
                WHERE run_key = ?
                ORDER BY recorded_at
                """,
                run_key,
            ),
            "final_results": _rows(
                connection,
                """
                SELECT rank, title, link, domain, final_score,
                       providers, provider_count
                FROM final_results
                WHERE run_key = ?
                ORDER BY rank
                LIMIT 50
                """,
                run_key,
            ),
        }
    finally:
        connection.close()


def postmortem_search_run(run_key: str, *, db_path: str | None = None) -> dict[str, Any]:
    connection = _connect(db_path)
    try:
        runs = _rows(
            connection,
            """
            SELECT run_key, CAST(recorded_at AS VARCHAR) AS recorded_at,
                   query, research_goal, intent, status, error_type, duration_ms,
                   provider_count, final_result_count, selected_providers,
                   skipped_providers
            FROM search_runs
            WHERE run_key = ?
            LIMIT 1
            """,
            run_key,
        )
        if not runs:
            raise LookupError(f"Search run '{run_key}' was not found.")

        provider_summary = _rows(
            connection,
            """
            SELECT provider,
                   COUNT(*) AS attempts,
                   COUNT(*) FILTER (WHERE status = 'success') AS successes,
                   COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS errors,
                   ROUND(AVG(latency_ms), 2) AS avg_latency_ms,
                   ARRAY_AGG(DISTINCT error_type) FILTER (WHERE error_type IS NOT NULL) AS error_types
            FROM provider_calls
            WHERE run_key = ?
            GROUP BY provider
            ORDER BY errors DESC, provider
            """,
            run_key,
        )
        rerank_summary = _rows(
            connection,
            """
            SELECT stage, status, error_type, input_count, output_count, duration_ms
            FROM rerank_stages
            WHERE run_key = ?
            ORDER BY recorded_at
            """,
            run_key,
        )
        return {
            "run_key": run_key,
            "run": runs[0],
            "provider_summary": provider_summary,
            "rerank_summary": rerank_summary,
            "next": [
                f"uv run web-search-cli search inspect --run-key {run_key}",
                f"uv run web-search-cli search postmortem --run-key {run_key}",
            ],
        }
    finally:
        connection.close()
