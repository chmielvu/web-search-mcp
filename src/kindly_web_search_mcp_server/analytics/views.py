"""Analytics view bootstrap for local DuckDB and MotherDuck."""

from __future__ import annotations

import threading

import duckdb

from .base_views import build_raw_view_sql
from .candidate_views import build_candidate_view_sql
from .derived_views import build_derived_view_sql
from .observability_store import (
    build_observability_view_sql,
    ensure_pipeline_observability_tables,
)
from .duckdb_store import (
    _db_path,
    _ensure_ab_assignments,
    _ensure_ab_experiment_variants,
    _ensure_ab_experiments,
    _ensure_ab_results,
    _ensure_ab_shadow_runs,
    _ensure_final_results,
    _ensure_merged_candidates,
    _ensure_provider_calls,
    _ensure_provider_candidates,
    _ensure_query_rewrites,
    _ensure_query_understanding,
    _ensure_rerank_candidates,
    _ensure_rerank_stages,
    _ensure_schema,
    _ensure_search_quality_scores,
    _ensure_search_runs,
    _ensure_judge_evaluations,
)
from .evals import build_eval_table_sql, build_eval_view_sql
from ..settings import settings

_LOCK = threading.Lock()


def _build_ab_view_sql(target: str) -> list[str]:
    return [
        f"""
        CREATE OR REPLACE VIEW {target}.v_ab_experiment_summary AS
        WITH variant_counts AS (
            SELECT experiment_id, COUNT(DISTINCT variant_name) AS cnt
            FROM {target}.ab_experiment_variants
            GROUP BY experiment_id
        ),
        assignment_counts AS (
            SELECT
                experiment_id,
                COUNT(DISTINCT assignment_id) AS cnt,
                COUNT(DISTINCT run_key) AS unique_runs
            FROM {target}.ab_assignments
            GROUP BY experiment_id
        ),
        result_agg AS (
            SELECT
                experiment_id,
                AVG(primary_metric) AS avg_primary,
                AVG(secondary_metric) AS avg_secondary,
                AVG(duration_ms) AS avg_dur,
                COUNT(result_id) AS cnt
            FROM {target}.ab_results
            GROUP BY experiment_id
        )
        SELECT
            e.experiment_id,
            e.layer,
            e.status,
            e.variant_a,
            e.variant_b,
            e.allocation_rate,
            e.min_sample_size,
            COALESCE(v.cnt, 0) AS variant_count,
            COALESCE(a.cnt, 0) AS assignment_count,
            COALESCE(a.unique_runs, 0) AS unique_run_count,
            r.avg_primary AS avg_primary_metric,
            r.avg_secondary AS avg_secondary_metric,
            r.avg_dur AS avg_duration_ms,
            COALESCE(r.cnt, 0) AS result_count
        FROM {target}.ab_experiments e
        LEFT JOIN variant_counts v ON e.experiment_id = v.experiment_id
        LEFT JOIN assignment_counts a ON e.experiment_id = a.experiment_id
        LEFT JOIN result_agg r ON e.experiment_id = r.experiment_id
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.v_ab_variant_comparison AS
        WITH variant_metrics AS (
            SELECT
                r.experiment_id,
                r.variant,
                COUNT(DISTINCT r.run_key) AS run_count,
                AVG(r.primary_metric) AS avg_primary_metric,
                AVG(r.secondary_metric) AS avg_secondary_metric,
                AVG(r.duration_ms) AS avg_duration_ms,
                STDDEV_SAMP(r.primary_metric) AS stddev_primary_metric,
                COUNT(r.result_id) AS result_count
            FROM {target}.ab_results r
            GROUP BY r.experiment_id, r.variant
        )
        SELECT
            e.experiment_id,
            e.layer,
            e.status,
            vm.variant,
            vm.run_count,
            vm.avg_primary_metric,
            vm.avg_secondary_metric,
            vm.avg_duration_ms,
            vm.stddev_primary_metric,
            vm.result_count,
            e.variant_a,
            e.variant_b,
            CASE
                WHEN vm.variant = e.variant_a THEN 'control'
                WHEN vm.variant = e.variant_b THEN 'treatment'
                ELSE 'other'
            END AS variant_role
        FROM {target}.ab_experiments e
        JOIN variant_metrics vm ON e.experiment_id = vm.experiment_id
        ORDER BY e.experiment_id, vm.variant
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.v_ab_shadow_run_analysis AS
        SELECT
            s.run_key,
            s.recorded_at,
            s.experiment_id,
            s.variant,
            s.layer,
            s.duration_ms AS shadow_duration_ms,
            s.judge_score,
            s.tokens_used,
            s.cost_usd,
            s.error_type,
            e.status AS experiment_status,
            e.variant_a,
            e.variant_b,
            CASE
                WHEN s.variant = e.variant_a THEN 'control'
                WHEN s.variant = e.variant_b THEN 'treatment'
                ELSE 'other'
            END AS variant_role,
            AVG(s.duration_ms) OVER (PARTITION BY s.experiment_id, s.variant) AS variant_avg_latency_ms,
            s.duration_ms - AVG(s.duration_ms) OVER (PARTITION BY s.experiment_id, s.variant) AS latency_delta_ms,
            AVG(s.judge_score) OVER (PARTITION BY s.experiment_id, s.variant) AS variant_avg_judge_score,
            s.judge_score - AVG(s.judge_score) OVER (PARTITION BY s.experiment_id, s.variant) AS judge_score_delta
        FROM {target}.ab_shadow_runs s
        LEFT JOIN {target}.ab_experiments e ON s.experiment_id = e.experiment_id
        """,
    ]


def _build_view_sql(
    target: str,
    *,
    source_table: str,
    include_ab_views: bool,
) -> list[str]:
    return [
        *build_raw_view_sql(target, source_table=source_table),
        *build_candidate_view_sql(target, source_table=source_table),
        *build_derived_view_sql(target, source_table=source_table),
        *build_observability_view_sql(target),
        *build_eval_view_sql(target),
        *(_build_ab_view_sql(target) if include_ab_views else []),
    ]


def ensure_views(*, db_path: str | None = None) -> None:
    """Create or replace all analytics views against the local DuckDB store."""
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    if not path.exists():
        return
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_schema(connection)
            _ensure_search_runs(connection)
            _ensure_query_understanding(connection)
            _ensure_query_rewrites(connection)
            _ensure_provider_calls(connection)
            _ensure_provider_candidates(connection)
            _ensure_merged_candidates(connection)
            _ensure_rerank_stages(connection)
            _ensure_rerank_candidates(connection)
            _ensure_final_results(connection)
            _ensure_search_quality_scores(connection)
            _ensure_judge_evaluations(connection)
            ensure_pipeline_observability_tables(db_path=db_path)
            for statement in build_eval_table_sql("main"):
                connection.execute(statement)
            _ensure_ab_experiments(connection)
            _ensure_ab_experiment_variants(connection)
            _ensure_ab_assignments(connection)
            _ensure_ab_results(connection)
            _ensure_ab_shadow_runs(connection)
            for statement in _build_view_sql(
                "main",
                source_table="search_events",
                include_ab_views=True,
            ):
                connection.execute(statement)
            # Judge relevance trend view
            connection.execute(
                """
                CREATE OR REPLACE VIEW main.v_judge_relevance_trend AS
                SELECT
                    DATE(recorded_at) as day,
                    judge_model,
                    COUNT(*) as evaluations,
                    AVG(relevance_raw) as avg_relevance_raw,
                    AVG(relevance_score) as avg_relevance_normalized,
                    AVG(duration_ms) as avg_latency_ms,
                    COUNT(CASE WHEN relevance_raw = 4 THEN 1 END) * 100.0 / COUNT(*) as pct_perfect,
                    COUNT(CASE WHEN relevance_raw = 1 THEN 1 END) * 100.0 / COUNT(*) as pct_irrelevant
                FROM judge_evaluations
                WHERE recorded_at > NOW() - INTERVAL '30 days'
                  AND relevance_raw IS NOT NULL
                GROUP BY DATE(recorded_at), judge_model
                """
            )
        finally:
            connection.close()


def refresh_views(*, db_path: str | None = None) -> None:
    """Recreate all views (useful after schema migrations)."""
    ensure_views(db_path=db_path)


ensure_local_views = ensure_views


def build_analytics_view_sql(schema: str) -> list[str]:
    """Return SQL statements to create analytics views in a remote schema."""
    return _build_view_sql(
        schema,
        source_table="analytics_event_raw",
        include_ab_views=False,
    )
