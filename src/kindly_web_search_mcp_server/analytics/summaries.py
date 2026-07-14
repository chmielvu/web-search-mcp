"""Refresh daily summary tables from raw pipeline tables."""

from __future__ import annotations

import threading

import duckdb

from ..settings import settings
from .duckdb_store import (
    _SUM_ID_TABLE_NAME,
    _SUM_PVD_TABLE_NAME,
    _SUM_QD_TABLE_NAME,
    _SUM_RD_TABLE_NAME,
    _db_path,
)

_LOCK = threading.Lock()


def refresh_summary_tables(db_path: str | None = None) -> None:
    """Upsert daily-aggregated rows into each summary table.

    Only processes rows from the last 2 days.  Guarded by
    ``settings.analytics_enabled`` -- returns immediately when disabled.
    """
    if not settings.analytics_enabled:
        return

    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_summary_tables(connection)

            # ── summary_provider_daily ─────────────────────────────────────
            connection.execute(
                f"""
                INSERT INTO {_SUM_PVD_TABLE_NAME} BY NAME
                SELECT
                    date_trunc('day', recorded_at)::DATE AS day,
                    provider,
                    count(*) AS query_count,
                    avg(num_results_returned) AS avg_results_returned,
                    approx_quantile(num_results_returned, 0.5) AS p50_results_returned,
                    avg(latency_ms) AS avg_latency_ms,
                    approx_quantile(latency_ms, 0.5) AS p50_latency_ms,
                    approx_quantile(latency_ms, 0.95) AS p95_latency_ms,
                    count(*) FILTER (WHERE error_type IS NOT NULL) * 1.0 / count(*) AS error_rate,
                    count(DISTINCT run_key) AS distinct_queries
                FROM provider_calls
                WHERE recorded_at >= now() - INTERVAL '2 days'
                GROUP BY ALL
                ON CONFLICT (day, provider) DO UPDATE SET
                    query_count = excluded.query_count,
                    avg_results_returned = excluded.avg_results_returned,
                    p50_results_returned = excluded.p50_results_returned,
                    avg_latency_ms = excluded.avg_latency_ms,
                    p50_latency_ms = excluded.p50_latency_ms,
                    p95_latency_ms = excluded.p95_latency_ms,
                    error_rate = excluded.error_rate,
                    distinct_queries = excluded.distinct_queries
                """
            )

            # ── summary_intent_daily ────────────────────────────────────────
            connection.execute(
                f"""
                INSERT INTO {_SUM_ID_TABLE_NAME} BY NAME
                SELECT
                    date_trunc('day', recorded_at)::DATE AS day,
                    intent,
                    count(*) AS query_count,
                    avg(understanding_confidence) AS avg_confidence,
                    avg(COALESCE(branch_count, 0)) AS avg_branch_count
                FROM search_runs
                WHERE recorded_at >= now() - INTERVAL '2 days'
                    AND intent IS NOT NULL
                GROUP BY ALL
                ON CONFLICT (day, intent) DO UPDATE SET
                    query_count = excluded.query_count,
                    avg_confidence = excluded.avg_confidence,
                    avg_branch_count = excluded.avg_branch_count
                """
            )

            # ── summary_rerank_daily ────────────────────────────────────────
            connection.execute(
                f"""
                INSERT INTO {_SUM_RD_TABLE_NAME} BY NAME
                SELECT
                    date_trunc('day', recorded_at)::DATE AS day,
                    stage,
                    COALESCE(provider, 'internal') AS provider,
                    count(*) AS runs_count,
                    avg(input_count * 1.0 / NULLIF(output_count, 0)) AS avg_compression_ratio,
                    avg(max_score) AS avg_max_score,
                    approx_quantile(duration_ms, 0.5) AS p50_latency_ms,
                    approx_quantile(duration_ms, 0.95) AS p95_latency_ms,
                    count(*) FILTER (WHERE entity_overlap_enabled) AS entity_overlap_runs
                FROM rerank_stages
                WHERE recorded_at >= now() - INTERVAL '2 days'
                GROUP BY ALL
                ON CONFLICT (day, stage, provider) DO UPDATE SET
                    runs_count = excluded.runs_count,
                    avg_compression_ratio = excluded.avg_compression_ratio,
                    avg_max_score = excluded.avg_max_score,
                    p50_latency_ms = excluded.p50_latency_ms,
                    p95_latency_ms = excluded.p95_latency_ms,
                    entity_overlap_runs = excluded.entity_overlap_runs
                """
            )

            # ── summary_quality_daily ───────────────────────────────────────
            connection.execute(
                f"""
                INSERT INTO {_SUM_QD_TABLE_NAME} BY NAME
                SELECT
                    date_trunc('day', recorded_at)::DATE AS day,
                    avg(provider_overlap_rate) AS avg_overlap_rate,
                    avg(domain_diversity_count) AS avg_domain_diversity,
                    avg(domain_diversity_ratio) AS avg_domain_diversity_ratio,
                    avg(rerank_compression_ratio) AS avg_compression_ratio,
                    avg(top_score) AS avg_top_score
                FROM search_quality_scores
                WHERE recorded_at >= now() - INTERVAL '2 days'
                GROUP BY ALL
                ON CONFLICT (day) DO UPDATE SET
                    avg_overlap_rate = excluded.avg_overlap_rate,
                    avg_domain_diversity = excluded.avg_domain_diversity,
                    avg_domain_diversity_ratio = excluded.avg_domain_diversity_ratio,
                    avg_compression_ratio = excluded.avg_compression_ratio,
                    avg_top_score = excluded.avg_top_score
                """
            )
        finally:
            connection.close()


def _ensure_summary_tables(connection: duckdb.DuckDBPyConnection) -> None:
    """Ensure all four summary tables exist."""
    from .duckdb_store import (
        _ensure_summary_intent_daily,
        _ensure_summary_provider_daily,
        _ensure_summary_quality_daily,
        _ensure_summary_rerank_daily,
    )

    _ensure_summary_provider_daily(connection)
    _ensure_summary_intent_daily(connection)
    _ensure_summary_rerank_daily(connection)
    _ensure_summary_quality_daily(connection)
