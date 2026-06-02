"""Deterministic read-only DuckDB analytics reports."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import duckdb
import pyarrow as pa

from ..settings import settings


def _db_path(db_path: str | None = None) -> Path:
    return Path(db_path or settings.analytics_duckdb_path)


def _run(sql: str, *, db_path: str | None = None) -> pa.Table:
    path = _db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Analytics DuckDB file does not exist: {path}")

    connection = duckdb.connect(str(path), read_only=True)
    try:
        return connection.execute(sql).to_arrow_table()
    finally:
        connection.close()


def provider_performance(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    window = max(1, int(days))
    sql = f"""
        WITH base AS (
            SELECT
                coalesce(provider, json_extract_string(payload_json, '$.provider_name')) AS provider_name,
                event_name,
                duration_ms,
                output_count
            FROM search_events
            WHERE event_name IN ('provider.search.result', 'provider.search.error')
              AND recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        )
        SELECT
            provider_name AS provider,
            COUNT(*) AS calls,
            COUNT(*) FILTER (WHERE event_name = 'provider.search.result') AS result_events,
            COUNT(*) FILTER (WHERE event_name = 'provider.search.error') AS error_events,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
            AVG(output_count) FILTER (WHERE event_name = 'provider.search.result') AS avg_results
        FROM base
        GROUP BY 1
        ORDER BY p95_ms NULLS LAST, provider
    """
    return _run(sql, db_path=db_path)


def cache_hit_rates(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    window = max(1, int(days))
    sql = f"""
        SELECT
            tool_name,
            cache_hit,
            COUNT(*) AS calls
        FROM search_events
        WHERE event_name IN ('tool.web_search.response', 'tool.academic_search.response')
          AND cache_hit IS NOT NULL
          AND recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY 1, 2
        ORDER BY tool_name, cache_hit
    """
    return _run(sql, db_path=db_path)


def rewrite_variant_quality(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    window = max(1, int(days))
    sql = f"""
        SELECT
            coalesce(json_extract_string(payload_json, '$.policy'), 'unknown') AS policy,
            coalesce(json_extract_string(payload_json, '$.intent'), 'unknown') AS intent,
            COUNT(*) AS rewrites,
            AVG(input_count) AS avg_variant_count,
            AVG(output_count) AS avg_final_query_count
        FROM search_events
        WHERE event_name = 'query.rewrite.completed'
          AND recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY 1, 2
        ORDER BY rewrites DESC, policy, intent
    """
    return _run(sql, db_path=db_path)


def fetch_quality(*, days: int = 30, db_path: str | None = None) -> pa.Table:
    window = max(1, int(days))
    sql = f"""
        SELECT
            coalesce(json_extract_string(payload_json, '$.fetch_backend'), 'unknown') AS fetch_backend,
            coalesce(json_extract_string(payload_json, '$.status'), 'unknown') AS status,
            COUNT(*) AS fetches,
            AVG(LENGTH(coalesce(json_extract_string(payload_json, '$.page_content'), ''))) AS avg_chars
        FROM search_events
        WHERE event_name = 'tool.get_content.response'
          AND recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY 1, 2
        ORDER BY fetches DESC, fetch_backend, status
    """
    return _run(sql, db_path=db_path)


def error_taxonomy(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    window = max(1, int(days))
    sql = f"""
        SELECT
            event_name,
            coalesce(tool_name, json_extract_string(payload_json, '$.tool_name'), 'unknown') AS tool_name,
            coalesce(provider, json_extract_string(payload_json, '$.provider'), json_extract_string(payload_json, '$.provider_name'), 'unknown') AS provider,
            coalesce(json_extract_string(payload_json, '$.error_type'), 'unknown') AS error_type,
            COUNT(*) AS errors,
            AVG(duration_ms) AS avg_duration_ms
        FROM search_events
        WHERE event_name LIKE '%.error'
          AND recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY 1, 2, 3, 4
        ORDER BY errors DESC, event_name, tool_name, provider, error_type
    """
    return _run(sql, db_path=db_path)


def candidate_survival(*, days: int = 7, db_path: str | None = None) -> pa.Table:
    window = max(1, int(days))
    sql = f"""
        SELECT
            stage,
            COUNT(*) AS rows,
            COUNT(DISTINCT run_key) AS runs,
            COUNT(DISTINCT url) AS unique_urls
        FROM main.vw_candidate_survival
        WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY 1
        ORDER BY CASE stage
            WHEN 'provider' THEN 1
            WHEN 'branch' THEN 2
            WHEN 'merged' THEN 3
            WHEN 'reranked' THEN 4
            WHEN 'final' THEN 5
            ELSE 99
        END
    """
    return _run(sql, db_path=db_path)


def eval_quality_summary(*, days: int = 30, db_path: str | None = None) -> pa.Table:
    window = max(1, int(days))
    sql = f"""
        WITH case_observations AS (
            SELECT
                eval_case_id,
                AVG(score) AS avg_observation_score,
                COUNT(*) FILTER (WHERE verdict = 'pass') AS passes,
                COUNT(*) FILTER (WHERE verdict = 'fail') AS fails
            FROM eval_observations
            GROUP BY 1
        ),
        llm_scores AS (
            SELECT
                eval_case_id,
                COUNT(*) AS score_rows,
                AVG(score_value) AS avg_llm_score
            FROM llm_quality_scores
            GROUP BY 1
        )
        SELECT
            r.suite_name,
            c.target_tool,
            COUNT(DISTINCT c.eval_case_id) AS cases,
            COUNT(DISTINCT r.eval_run_id) AS runs,
            SUM(COALESCE(o.passes, 0)) AS passes,
            SUM(COALESCE(o.fails, 0)) AS fails,
            AVG(o.avg_observation_score) AS avg_score,
            SUM(COALESCE(q.score_rows, 0)) AS llm_score_rows,
            AVG(q.avg_llm_score) AS avg_llm_score
        FROM eval_runs AS r
        LEFT JOIN eval_cases AS c
          ON c.eval_run_id = r.eval_run_id
        LEFT JOIN case_observations AS o
          ON o.eval_case_id = c.eval_case_id
        LEFT JOIN llm_scores AS q
          ON q.eval_case_id = c.eval_case_id
        WHERE r.created_at >= CURRENT_TIMESTAMP - INTERVAL '{window} days'
        GROUP BY 1, 2
        ORDER BY cases DESC, suite_name, target_tool
    """
    return _run(sql, db_path=db_path)


_REPORTS: dict[str, Callable[..., pa.Table]] = {
    "provider-performance": provider_performance,
    "cache-hit-rates": cache_hit_rates,
    "rewrite-variant-quality": rewrite_variant_quality,
    "fetch-quality": fetch_quality,
    "error-taxonomy": error_taxonomy,
    "candidate-survival": candidate_survival,
    "eval-quality-summary": eval_quality_summary,
}


def available_reports() -> tuple[str, ...]:
    return tuple(sorted(_REPORTS))


def run_report(
    report_name: str,
    *,
    days: int = 7,
    db_path: str | None = None,
) -> pa.Table:
    try:
        report_fn = _REPORTS[report_name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown analytics report {report_name!r}. "
            f"Available reports: {', '.join(available_reports())}."
        ) from exc
    return report_fn(days=days, db_path=db_path)
