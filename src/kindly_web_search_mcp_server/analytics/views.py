"""Analytics view bootstrap — 10 human-readable dashboard views.

Replaces the 16 JSON-parsing views and 7 observability views with clean,
dashboard-ready views using CASE labels, ROUND, COALESCE, quantile_cont,
and date_trunc.  A/B and eval views are preserved.
"""

from __future__ import annotations

import threading

import duckdb

from .duckdb_store import (
    _db_path,
    _ensure_ab_assignments,
    _ensure_ab_experiment_variants,
    _ensure_ab_experiments,
    _ensure_ab_results,
    _ensure_ab_shadow_runs,
    ensure_store_schema,
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


def _build_dashboard_view_sql(target: str) -> list[str]:
    """Return SQL for the 11 human-readable dashboard views."""
    t = target
    return [
        # 1. Run-level summary
        f"""
        CREATE OR REPLACE VIEW {t}.vw_run_summary AS
        SELECT
            run_key,
            recorded_at,
            query,
            intent,
            CASE
                WHEN status = 'success' THEN '✅ Success'
                WHEN status = 'partial' THEN '⚠️ Partial'
                WHEN status = 'error' THEN '❌ Error'
                ELSE COALESCE(status, 'unknown')
            END AS status_label,
            final_result_count,
            candidate_count,
            CASE
                WHEN duration_ms < 5000 THEN 'Fast (<5s)'
                WHEN duration_ms < 15000 THEN 'Normal (5-15s)'
                WHEN duration_ms < 30000 THEN 'Slow (15-30s)'
                ELSE 'Very Slow (>30s)'
            END AS latency_tier,
            ROUND(duration_ms / 1000.0, 2) AS duration_s,
            rewrite_enabled,
            rewrite_model,
            ROUND(rewrite_latency_ms / 1000.0, 2) AS rewrite_latency_s,
            rewrite_error,
            selected_providers,
            skipped_providers
        FROM search_runs
        ORDER BY recorded_at DESC
        """,
        # 2. Provider performance
        f"""
        CREATE OR REPLACE VIEW {t}.vw_provider_performance AS
        SELECT
            provider,
            COUNT(*) AS total_calls,
            COUNT(*) FILTER (WHERE status = 'success') AS success_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'success') / COUNT(*), 1) AS success_rate_pct,
            ROUND(AVG(latency_ms), 0) AS avg_latency_ms,
            ROUND(quantile_cont(latency_ms, 0.95), 0) AS p95_latency_ms,
            SUM(num_results_returned) AS total_results_returned,
            COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count,
            MODE(error_type) AS most_common_error
        FROM provider_calls
        GROUP BY provider
        ORDER BY total_calls DESC
        """,
        # 3. Branch summary
        f"""
        CREATE OR REPLACE VIEW {t}.vw_branch_summary AS
        SELECT
            run_key,
            branch_index,
            branch_role,
            branch_query,
            branch_why,
            support_terms,
            assigned_providers,
            attempted_providers,
            results_count,
            ROUND(latency_ms / 1000.0, 2) AS latency_s
        FROM search_branches
        ORDER BY run_key, branch_index
        """,
        # 4. Candidate survival funnel
        f"""
        CREATE OR REPLACE VIEW {t}.vw_candidate_funnel AS
        SELECT
            c.run_key,
            c.link,
            c.title,
            c.domain,
            ROUND(c.rrf_score, 4) AS rrf_score,
            c.provider_count,
            c.providers,
            c.overlap_flag,
            (SELECT rc.rank_after FROM rerank_candidates rc
             WHERE rc.run_key = c.run_key AND rc.link = c.link AND rc.stage = 'bi_encoder'
             LIMIT 1) AS bi_rank,
            (SELECT rc.rank_after FROM rerank_candidates rc
             WHERE rc.run_key = c.run_key AND rc.link = c.link AND rc.stage = 'cross_encoder'
             LIMIT 1) AS cross_rank,
            (SELECT rc.rank_after FROM rerank_candidates rc
             WHERE rc.run_key = c.run_key AND rc.link = c.link AND rc.stage = 'rankllm'
             LIMIT 1) AS rankllm_rank,
            f.rank AS final_rank,
            (SELECT rc.cross_encoder_raw FROM rerank_candidates rc
             WHERE rc.run_key = c.run_key AND rc.link = c.link AND rc.stage = 'cross_encoder'
             LIMIT 1) AS cross_encoder_raw,
            (f.rank IS NOT NULL) AS in_final_results
        FROM search_candidates c
        LEFT JOIN final_results f ON c.run_key = f.run_key AND c.link = f.link
        ORDER BY c.run_key, COALESCE(f.rank, 9999)
        """,
        # 5. Rerank stage timeline
        f"""
        CREATE OR REPLACE VIEW {t}.vw_rerank_timeline AS
        SELECT
            rs.run_key,
            rs.recorded_at,
            rs.stage,
            CASE
                WHEN rs.stage = 'bi_encoder' THEN '1. Bi-Encoder'
                WHEN rs.stage = 'cross_encoder' THEN '2. Cross-Encoder'
                WHEN rs.stage = 'rankllm' THEN '3. RankLLM'
                ELSE rs.stage
            END AS stage_label,
            rs.provider, rs.model,
            rs.input_count, rs.output_count,
            ROUND(100.0 * rs.output_count / NULLIF(rs.input_count, 0), 1) AS survival_rate_pct,
            ROUND(rs.duration_ms, 1) AS duration_ms,
            ROUND(rs.max_score, 4) AS max_score,
            ROUND(rs.avg_score, 4) AS avg_score,
            rs.status, rs.error_type,
            rs.input_tokens, rs.output_tokens
        FROM rerank_stages rs
        WHERE rs.stage IN ('bi_encoder', 'cross_encoder', 'rankllm')
        ORDER BY rs.run_key,
            CASE
                WHEN rs.stage = 'bi_encoder' THEN 1
                WHEN rs.stage = 'cross_encoder' THEN 2
                WHEN rs.stage = 'rankllm' THEN 3
                ELSE 4
            END
        """,
        # 6. Rewrite diagnostics
        f"""
        CREATE OR REPLACE VIEW {t}.vw_rewrite_diagnostics AS
        SELECT
            r.run_key, r.recorded_at,
            r.query, r.rewrite_enabled,
            r.rewrite_model,
            r.rewrite_input_tokens, r.rewrite_output_tokens,
            ROUND(r.rewrite_latency_ms, 1) AS rewrite_latency_ms,
            r.rewrite_error,
            r.rake_terms, r.brave_autosuggest, r.brave_spellcheck,
            r.selected_providers, r.skipped_providers
        FROM search_runs r
        WHERE r.rewrite_enabled = true
        ORDER BY r.recorded_at DESC
        """,
        # 7. Daily trend (gap-free calendar)
        f"""
        CREATE OR REPLACE VIEW {t}.vw_daily_trend AS
        WITH date_series AS (
            SELECT generate_series(
                date_trunc('day', CURRENT_TIMESTAMP - INTERVAL '30 days')::TIMESTAMP,
                date_trunc('day', CURRENT_TIMESTAMP)::TIMESTAMP,
                INTERVAL '1 day'
            ) AS day_bucket
        ),
        daily_stats AS (
            SELECT
                date_trunc('day', recorded_at) AS day_bucket,
                COUNT(*) AS run_count,
                COUNT(*) FILTER (WHERE status = 'success') AS success_count,
                ROUND(AVG(duration_ms), 1) AS avg_latency_ms,
                ROUND(quantile_cont(duration_ms, 0.95), 1) AS p95_latency_ms,
                COUNT(*) FILTER (WHERE final_result_count = 0) AS zero_result_count
            FROM search_runs
            WHERE recorded_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
            GROUP BY 1
        )
        SELECT
            ds.day_bucket,
            COALESCE(dst.run_count, 0) AS run_count,
            COALESCE(dst.success_count, 0) AS success_count,
            COALESCE(ROUND(100.0 * dst.success_count / NULLIF(dst.run_count, 0), 1), 0) AS success_rate_pct,
            COALESCE(dst.avg_latency_ms, 0) AS avg_latency_ms,
            COALESCE(dst.p95_latency_ms, 0) AS p95_latency_ms,
            COALESCE(dst.zero_result_count, 0) AS zero_result_count
        FROM date_series ds
        LEFT JOIN daily_stats dst ON ds.day_bucket = dst.day_bucket
        ORDER BY ds.day_bucket DESC
        """,
        # 8. Quality score distribution
        f"""
        CREATE OR REPLACE VIEW {t}.vw_quality_distribution AS
        SELECT
            relevance_grade,
            CASE
                WHEN overall_score >= 0.8 THEN 'Excellent (0.8-1.0)'
                WHEN overall_score >= 0.6 THEN 'Good (0.6-0.8)'
                WHEN overall_score >= 0.4 THEN 'Fair (0.4-0.6)'
                WHEN overall_score >= 0.2 THEN 'Poor (0.2-0.4)'
                ELSE 'Very Poor (0.0-0.2)'
            END AS quality_tier,
            COUNT(*) AS result_count,
            ROUND(AVG(overall_score), 3) AS avg_score,
            ROUND(quantile_cont(overall_score, 0.50), 3) AS median_score
        FROM judge_evaluations
        WHERE evaluated_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'
        GROUP BY relevance_grade, quality_tier
        ORDER BY relevance_grade, quality_tier
        """,
        # 9. Provider health
        f"""
        CREATE OR REPLACE VIEW {t}.vw_provider_health AS
        SELECT * FROM provider_health_transitions ORDER BY recorded_at DESC
        """,
        # 10. Judge quality summary
        f"""
        CREATE OR REPLACE VIEW {t}.vw_judge_quality AS
        SELECT
            je.run_key,
            je.link,
            je.relevance_grade,
            CASE
                WHEN je.relevance_grade = 'excellent' THEN '🟢 Excellent'
                WHEN je.relevance_grade = 'good' THEN '🟢 Good'
                WHEN je.relevance_grade = 'fair' THEN '🟡 Fair'
                WHEN je.relevance_grade = 'poor' THEN '🔴 Poor'
                ELSE COALESCE(je.relevance_grade, 'unrated')
            END AS grade_label,
            ROUND(je.relevance_score, 3) AS relevance_score,
            ROUND(je.accuracy_score, 3) AS accuracy_score,
            ROUND(je.completeness_score, 3) AS completeness_score,
            ROUND(je.source_quality_score, 3) AS source_quality_score,
            ROUND(je.overall_score, 3) AS overall_score,
            CASE
                WHEN je.overall_score >= 0.8 THEN 'High quality'
                WHEN je.overall_score >= 0.6 THEN 'Acceptable'
                WHEN je.overall_score >= 0.4 THEN 'Needs improvement'
                ELSE 'Low quality'
            END AS quality_label,
            je.rationale,
            je.evaluated_at
        FROM judge_evaluations je
        ORDER BY je.evaluated_at DESC
        """,
        # 11. LLM judgments — read-only view over persisted FlockMTL verdicts.
        # Each row is one already-billed LLM call from
        # `analytics/judges.py::judge_search_run`. Querying this view does
        # NOT burn Mistral credits — the verdicts are persisted.
        f"""
        CREATE OR REPLACE VIEW {t}.vw_llm_judgments AS
        SELECT
            recorded_at,
            run_key,
            judgment_kind,
            judgment_target,
            prompt_name,
            model_name,
            verdict,
            facet,
            reasoning,
            rubric_version,
            confidence,
            context_shown,
            status,
            input_tokens,
            output_tokens,
            duration_ms,
            error_message
        FROM llm_judgments
        ORDER BY recorded_at DESC
        """,
        # 12. FlockMTL resource catalog — what MODELs and PROMPTs are
        # registered. Backed by the `flockmtl_resources` metadata table
        # populated by ensure_flockmtl_resources.
        f"""
        CREATE OR REPLACE VIEW {t}.vw_flockmtl_resources AS
        SELECT
            kind,
            name,
            definition,
            registered_at
        FROM flockmtl_resources
        ORDER BY kind, name
        """,
        # 13. Judge facet aggregation — per-day, per-facet aggregates
        # over the persisted judge verdicts. Deliberately facet-grained
        # (not collapsed into a single run-quality score) so the canon
        # "a single score hides actionable failures" stays honored.
        # Trend drift queries filter by `rubric_version`.
        f"""
        CREATE OR REPLACE VIEW {t}.vw_judge_facet_agg AS
        SELECT
            date_trunc('day', recorded_at) AS day,
            facet,
            judgment_kind,
            model_name,
            rubric_version,
            COUNT(*) AS total_rows,
            COUNT(*) FILTER (WHERE status='success') AS success_rows,
            ROUND(
                COUNT(*) FILTER (WHERE status='success')::DOUBLE
                / NULLIF(COUNT(*), 0),
                3
            ) AS success_rate,
            ROUND(AVG(confidence), 3) AS avg_confidence,
            ROUND(quantile_cont(confidence, 0.50), 3) AS median_confidence
        FROM llm_judgments
        GROUP BY 1, 2, 3, 4, 5
        ORDER BY 1 DESC, 2, 3
        """,
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
            # Ensure all tables exist (9 pipeline + health + quality + judge + vss)
            ensure_store_schema(db_path=db_path)
            # Eval tables
            for statement in build_eval_table_sql("main"):
                connection.execute(statement)
            # A/B tables
            _ensure_ab_experiments(connection)
            _ensure_ab_experiment_variants(connection)
            _ensure_ab_assignments(connection)
            _ensure_ab_results(connection)
            _ensure_ab_shadow_runs(connection)
            # Dashboard views
            for statement in _build_dashboard_view_sql("main"):
                connection.execute(statement)
            # Eval views
            for statement in build_eval_view_sql("main"):
                connection.execute(statement)
            # A/B views
            for statement in _build_ab_view_sql("main"):
                connection.execute(statement)
        finally:
            connection.close()


def refresh_views(*, db_path: str | None = None) -> None:
    """Recreate all views (useful after schema migrations)."""
    ensure_views(db_path=db_path)


ensure_local_views = ensure_views


def build_analytics_view_sql(schema: str) -> list[str]:
    """Return SQL statements to create analytics views in a remote schema."""
    return [
        *_build_dashboard_view_sql(schema),
        *build_eval_view_sql(schema),
    ]
