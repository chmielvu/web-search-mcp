"""Local DuckDB analytics queries against the verified live schema."""

from __future__ import annotations

from pathlib import Path

import duckdb

from ..settings import settings
from .formatting import json_safe_rows

# Verified tables: search_runs, provider_calls, rerank_stages, final_results, search_candidates
# Verified views: vw_run_summary, vw_provider_performance, vw_eval_provider_quality


def _db_path(db_path: str | None = None) -> Path:
    return Path(db_path or settings.analytics_duckdb_path)


def _limit(max_rows: int) -> int:
    return max(1, min(int(max_rows), 500))


# ---------------------------------------------------------------------------
# Query builders — each returns (sql, rationale)
# ---------------------------------------------------------------------------


def _provider_query(limit: int) -> tuple[str, str]:
    """Per-provider call stats from provider_calls."""
    sql = f"""
        SELECT
            provider,
            COUNT(*) AS total_calls,
            COUNT(*) FILTER (WHERE status = 'success') AS success_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'success') / NULLIF(COUNT(*), 0), 1) AS success_rate_pct,
            ROUND(AVG(latency_ms), 0) AS avg_latency_ms,
            ROUND(quantile_cont(latency_ms, 0.95), 0) AS p95_latency_ms,
            SUM(num_results_returned) AS total_results_returned,
            COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count,
            MODE(error_type) AS most_common_error
        FROM provider_calls
        GROUP BY 1
        ORDER BY total_calls DESC, provider
        LIMIT {limit}
    """
    return sql, "provider"


def _error_query(limit: int) -> tuple[str, str]:
    """Error breakdown from provider_calls and rerank_stages."""
    sql = f"""
        SELECT
            provider,
            error_type,
            COUNT(*) AS occurrences,
            AVG(latency_ms) AS avg_latency_ms,
            MODE(status) AS common_status
        FROM provider_calls
        WHERE error_type IS NOT NULL
        GROUP BY 1, 2
        UNION ALL
        SELECT
            COALESCE(provider, model) AS provider,
            error_type,
            COUNT(*) AS occurrences,
            AVG(duration_ms) AS avg_latency_ms,
            MODE(status) AS common_status
        FROM rerank_stages
        WHERE error_type IS NOT NULL
        GROUP BY 1, 2
        ORDER BY occurrences DESC, provider, error_type
        LIMIT {limit}
    """
    return sql, "error"


def _eval_query(limit: int) -> tuple[str, str]:
    """Eval suite quality scores from vw_eval_provider_quality."""
    sql = f"""
        SELECT
            suite_name,
            target_tool,
            COUNT(*) AS cases,
            COUNT(*) FILTER (WHERE passes > 0) AS cases_with_passes,
            AVG(avg_score) AS avg_score
        FROM vw_eval_provider_quality
        GROUP BY 1, 2
        ORDER BY cases DESC, suite_name, target_tool
        LIMIT {limit}
    """
    return sql, "eval"


def _recent_events_query(limit: int) -> tuple[str, str]:
    """Recent search runs from vw_run_summary."""
    sql = f"""
        SELECT
            recorded_at,
            query,
            intent,
            status_label,
            final_result_count,
            latency_tier,
            duration_s,
            rewrite_enabled,
            rewrite_model,
            rewrite_latency_s,
            rewrite_error,
            selected_providers,
            skipped_providers
        FROM vw_run_summary
        ORDER BY recorded_at DESC
        LIMIT {limit}
    """
    return sql, "events"


def _rerank_query(limit: int) -> tuple[str, str]:
    """Rerank stage metrics from rerank_stages."""
    sql = f"""
        SELECT
            stage,
            provider,
            model,
            COUNT(*) AS stage_runs,
            SUM(input_count) AS total_input_count,
            SUM(output_count) AS total_output_count,
            ROUND(100.0 * SUM(output_count) / NULLIF(SUM(input_count), 0), 1) AS survival_rate_pct,
            ROUND(AVG(duration_ms), 1) AS avg_duration_ms,
            ROUND(AVG(max_score), 4) AS avg_max_score,
            ROUND(AVG(avg_score), 4) AS avg_score,
            COUNT(*) FILTER (WHERE status = 'success') AS success_count,
            COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count,
            MODE(error_type) AS most_common_error,
            SUM(input_tokens) AS total_input_tokens,
            SUM(output_tokens) AS total_output_tokens
        FROM rerank_stages
        WHERE stage IN ('bi_encoder', 'cross_encoder', 'rankllm')
        GROUP BY 1, 2, 3
        ORDER BY stage_runs DESC, stage, provider, model
        LIMIT {limit}
    """
    return sql, "rerank"


def _latency_query(limit: int) -> tuple[str, str]:
    """Latency distribution from search_runs."""
    sql = f"""
        SELECT
            CASE
                WHEN duration_ms < 2000  THEN '0-2s'
                WHEN duration_ms < 5000  THEN '2-5s'
                WHEN duration_ms < 10000 THEN '5-10s'
                WHEN duration_ms < 20000 THEN '10-20s'
                WHEN duration_ms < 30000 THEN '20-30s'
                ELSE '30s+'
            END AS latency_bucket,
            COUNT(*) AS run_count,
            COUNT(*) FILTER (WHERE status = 'success') AS success_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'success') / NULLIF(COUNT(*), 0), 1) AS success_rate_pct,
            ROUND(AVG(duration_ms), 0) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95), 0) AS p95_duration_ms,
            ROUND(AVG(final_result_count), 1) AS avg_result_count,
            COUNT(DISTINCT run_key) AS distinct_runs
        FROM search_runs
        GROUP BY 1
        ORDER BY MIN(duration_ms)
        LIMIT {limit}
    """
    return sql, "latency"


def _run_quality_query(limit: int) -> tuple[str, str]:
    """Run quality summary from vw_eval_provider_quality."""
    sql = f"""
        SELECT
            suite_name,
            COUNT(DISTINCT target_tool) AS tools_tested,
            SUM(cases) AS total_cases,
            SUM(passes) AS total_passes,
            SUM(fails) AS total_fails,
            ROUND(100.0 * SUM(passes) / NULLIF(SUM(cases), 0), 1) AS pass_rate_pct,
            ROUND(AVG(avg_score), 3) AS avg_score,
            MIN(avg_score) AS min_score,
            MAX(avg_score) AS max_score
        FROM vw_eval_provider_quality
        GROUP BY 1
        ORDER BY total_cases DESC, suite_name
        LIMIT {limit}
    """
    return sql, "run_quality"


def build_local_analytics_query_sql(question: str, *, max_rows: int = 100) -> tuple[str, str]:
    """Dispatch local analytics query based on question keywords."""
    limit = _limit(max_rows)
    q = question.lower().strip()

    if "rerank" in q or "cross encode" in q or "bi-encode" in q or "rankllm" in q:
        return _rerank_query(limit)
    if "latency" in q or "speed" in q or "duration" in q or "fast" in q or "slow" in q:
        return _latency_query(limit)
    if ("quality" in q or "score" in q or "verdict" in q) and (
        "run" in q or "result" in q or "outcome" in q
    ):
        return _run_quality_query(limit)
    if "provider" in q or "searxng" in q or "brave" in q or "tavily" in q:
        return _provider_query(limit)
    if "error" in q or "failure" in q or "timeout" in q or "exception" in q:
        return _error_query(limit)
    if "eval" in q or "quality score" in q or "suite" in q:
        return _eval_query(limit)
    if "recent" in q or "latest" in q or "timeline" in q or "activity" in q or "event" in q:
        return _recent_events_query(limit)

    raise ValueError(
        "Could not classify the analytics question. "
        "Supported topics: rerank, latency, run quality, provider, error, eval, recent events."
    )


def run_local_analytics_query(
    question: str,
    *,
    max_rows: int = 100,
    db_path: str | None = None,
) -> dict[str, object]:
    path = _db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Analytics DuckDB file does not exist: {path}")

    connection = duckdb.connect(str(path), read_only=True)
    try:
        sql, rationale = build_local_analytics_query_sql(question, max_rows=max_rows)
        table = connection.execute(sql).to_arrow_table()
    finally:
        connection.close()

    return {
        "question": question,
        "scope": "local",
        "view_prefix": "main.",
        "rationale": rationale,
        "sql": sql.strip(),
        "row_count": table.num_rows,
        "rows": json_safe_rows(table.to_pylist()),
    }
