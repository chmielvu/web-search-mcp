"""Analytics view bootstrap — 18 dashboard, quality-diagnostic, A/B, and eval views.

Uses CASE labels, ROUND, COALESCE, quantile_cont, and date_trunc.  Quality-diagnostic
views join llm_judgments to search_runs and final_results; calibration views are
confidence-only and never fabricate human labels.
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
            ROUND(quantile_cont(latency_ms, 0.50), 0) AS p50_latency_ms,
            ROUND(quantile_cont(latency_ms, 0.95), 0) AS p95_latency_ms,
            SUM(num_results_returned) AS total_results_returned,
            COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count,
            COUNT(*) FILTER (WHERE result_class = 'empty') AS empty_count,
            COUNT(*) FILTER (WHERE result_class = 'timeout') AS timeout_count,
            COUNT(*) FILTER (WHERE result_class = 'incomplete') AS incomplete_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE result_class = 'empty') / COUNT(*), 1) AS empty_rate_pct,
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
            r.rake_terms, r.brave_autosuggest,
            r.selected_providers, r.skipped_providers
        FROM search_runs r
        WHERE r.rewrite_enabled = true
        ORDER BY r.recorded_at DESC
        """,
        # 7. Daily trend (gap-free calendar)
        f"""
        CREATE OR REPLACE VIEW {t}.vw_daily_trend AS
        WITH date_series AS (
            SELECT CAST(unnest(generate_series(
                date_trunc('day', CURRENT_TIMESTAMP - INTERVAL '30 days'),
                date_trunc('day', CURRENT_TIMESTAMP),
                INTERVAL '1 day'
            )) AS DATE) AS day_bucket
        ),
        daily_stats AS (
            SELECT
                CAST(date_trunc('day', recorded_at) AS DATE) AS day_bucket,
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
          AND status = 'success'
        GROUP BY ALL
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
        # 14. Legacy four-dimensional judge health.
        f"""
        CREATE OR REPLACE VIEW {t}.vw_legacy_judge_quality AS
        SELECT
            run_key,
            recorded_at,
            evaluated_at,
            tool_name,
            judge_model,
            model_used,
            link,
            relevance_grade,
            relevance_score,
            accuracy_grade,
            accuracy_score,
            completeness_grade,
            completeness_score,
            source_quality_grade,
            source_quality_score,
            overall_score,
            status,
            error_type,
            error_message,
            status = 'success' AND overall_score IS NOT NULL AS usable_row,
            rationale
        FROM judge_evaluations
        """,
        # 15. Typed result-quality diagnostics at result grain.
        f"""
        CREATE OR REPLACE VIEW {t}.vw_result_quality_diagnostics AS
        SELECT
            lj.recorded_at,
            lj.run_key,
            lj.judgment_target AS link,
            sr.query,
            sr.research_goal,
            sr.intent,
            sr.understanding_confidence AS classifier_confidence,
            fr.rank,
            fr.providers,
            fr.provider_count,
            lj.status AS judge_status,
            lj.error_message AS judge_error,
            try_cast(json_extract(lj.payload_json, '$.parsed.intent_match') AS BOOLEAN) AS intent_match,
            try_cast(json_extract(lj.payload_json, '$.parsed.informativeness') AS INTEGER) AS informativeness,
            lj.confidence AS judge_confidence,
            CASE
                WHEN fr.rank IS NULL THEN 'missing_provenance'
                WHEN fr.providers IS NULL OR len(fr.providers) = 0 THEN 'missing_provider'
                ELSE 'complete'
            END AS provenance_status,
            CASE
                WHEN fr.rank IS NULL THEN NULL
                WHEN fr.rank <= 3 THEN '1-3'
                WHEN fr.rank <= 10 THEN '4-10'
                ELSE '11+'
            END AS rank_bucket
        FROM llm_judgments lj
        LEFT JOIN search_runs sr ON sr.run_key = lj.run_key
        LEFT JOIN final_results fr
            ON fr.run_key = lj.run_key AND fr.link = lj.judgment_target
        WHERE lj.judgment_kind = 'result_quality'
        """,
        # 16. Misses grouped by stable, available diagnostics.
        f"""
        CREATE OR REPLACE VIEW {t}.vw_quality_miss_summary AS
        SELECT
            date_trunc('day', recorded_at) AS day,
            intent,
            COALESCE(array_to_string(providers, ','), 'unknown') AS provider_group,
            rank_bucket,
            provenance_status,
            CASE
                WHEN classifier_confidence IS NULL THEN 'unknown'
                WHEN classifier_confidence < 0.50 THEN '0.00-0.49'
                WHEN classifier_confidence < 0.75 THEN '0.50-0.74'
                WHEN classifier_confidence < 0.90 THEN '0.75-0.89'
                ELSE '0.90-1.00'
            END AS confidence_bucket,
            COUNT(*) AS judged_results,
            COUNT(*) FILTER (WHERE intent_match = false) AS intent_misses,
            COUNT(*) FILTER (WHERE informativeness <= 2) AS low_informativeness,
            COUNT(*) FILTER (WHERE judge_status <> 'success') AS judge_errors,
            ROUND(AVG(classifier_confidence), 3) AS avg_classifier_confidence
        FROM {t}.vw_result_quality_diagnostics
        GROUP BY ALL
        ORDER BY day DESC, judged_results DESC
        """,
        # 17. Query understanding events joined to canonical search outcomes.
        f"""
        CREATE OR REPLACE VIEW {t}.vw_query_understanding_events AS
        SELECT
            q.recorded_at,
            q.run_key,
            q.tool_call_id,
            q.session_id,
            q.raw_query,
            q.normalized_query,
            q.research_goal,
            q.predicted_intent,
            q.predicted_confidence,
            q.final_intent,
            q.final_confidence,
            q.decision_path,
            q.fallback_reason,
            q.classifier_model,
            q.classifier_provider,
            q.classifier_endpoint,
            q.classifier_latency_ms,
            q.confidence_threshold,
            q.scores_json,
            q.entities_json,
            q.preserved_terms,
            q.compared_entities,
            q.time_sensitivity,
            q.domain_hints,
            q.should_decompose,
            q.rationale,
            sr.final_result_count,
            sr.provider_count,
            sr.status AS search_status,
            sr.duration_ms AS search_duration_ms
        FROM query_understanding_events q
        LEFT JOIN search_runs sr ON sr.run_key = q.run_key
        """,
        # 18. Calibration-safe confidence report. Production rows are unlabeled
        # unless a human adjudication row is explicitly present.
        f"""
        CREATE OR REPLACE VIEW {t}.vw_query_understanding_calibration AS
        WITH labeled AS (
            SELECT
                q.*,
                cs.human_verdict,
                CASE
                    WHEN cs.human_verdict IS NOT NULL THEN 'human'
                    ELSE 'unlabeled'
                END AS label_source,
                CASE
                    WHEN cs.human_verdict IS NOT NULL
                    THEN (q.final_intent = cs.human_verdict)
                    ELSE NULL
                END AS observed_agreement
            FROM query_understanding_events q
            LEFT JOIN judge_calibration_set cs
                ON cs.run_key = q.run_key AND cs.facet = 'query_understanding'
        )
        SELECT
            CASE
                WHEN final_confidence < 0.50 THEN '0.00-0.49'
                WHEN final_confidence < 0.75 THEN '0.50-0.74'
                WHEN final_confidence < 0.90 THEN '0.75-0.89'
                ELSE '0.90-1.00'
            END AS confidence_bucket,
            decision_path,
            label_source,
            COUNT(*) AS event_count,
            ROUND(AVG(final_confidence), 3) AS avg_confidence,
            ROUND(AVG(observed_agreement::INTEGER), 3) AS observed_agreement,
            ROUND(
                AVG(CASE WHEN label_source = 'human'
                    THEN POWER(final_confidence - observed_agreement::INTEGER, 2)
                    ELSE NULL
                END),
                4
            ) AS brier_score
        FROM labeled
        GROUP BY ALL
        ORDER BY confidence_bucket, decision_path, label_source
        """,
        # 19. Quality metrics by intent
        f"""
        CREATE OR REPLACE VIEW {t}.vw_quality_by_intent AS
        SELECT
            sr.intent,
            COUNT(*) AS total_runs,
            COUNT(*) FILTER (WHERE sr.status = 'success') AS success_runs,
            COUNT(*) FILTER (WHERE sr.final_result_count = 0) AS zero_result_runs,
            ROUND(100.0 * COUNT(*) FILTER (WHERE sr.final_result_count = 0) / NULLIF(COUNT(*), 0), 2) AS zero_result_pct,
            ROUND(AVG(sr.duration_ms) / 1000.0, 2) AS avg_duration_s,
            ROUND(quantile_cont(sr.duration_ms, 0.95) / 1000.0, 2) AS p95_duration_s,
            ROUND(AVG(sqs.provider_overlap_rate), 3) AS avg_provider_overlap,
            ROUND(AVG(sqs.domain_diversity_ratio), 3) AS avg_domain_diversity
        FROM search_runs sr
        LEFT JOIN search_quality_scores sqs ON sr.run_key = sqs.run_key
        GROUP BY sr.intent
        ORDER BY total_runs DESC
        """,
        # 20. Bad case queue (zero-result & failed run triage)
        f"""
        CREATE OR REPLACE VIEW {t}.vw_bad_case_queue AS
        SELECT
            sr.recorded_at,
            sr.run_key,
            sr.query,
            sr.intent,
            sr.status,
            CASE
                WHEN sr.candidate_count = 0 THEN 'retrieval_empty'
                WHEN sr.merged_count > 0 AND sr.final_result_count = 0 THEN 'rerank_filtered_all'
                WHEN sr.status = 'error' THEN 'pipeline_error'
                ELSE 'other'
            END AS failure_reason,
            sr.selected_providers,
            sr.error_type,
            'pending' AS annotation_status
        FROM search_runs sr
        WHERE sr.final_result_count = 0 OR sr.status <> 'success'
        ORDER BY sr.recorded_at DESC
        """,
        # 21. Run funnel by stage
        f"""
        CREATE OR REPLACE VIEW {t}.vw_run_funnel_by_stage AS
        SELECT
            run_key,
            recorded_at,
            query,
            intent,
            candidate_count AS raw_candidates,
            merged_count AS after_merge,
            try_cast(json_extract(payload_json, '$.funnel_counts.cross_output_count') AS INTEGER) AS after_cross_encoder,
            try_cast(json_extract(payload_json, '$.funnel_counts.rankllm_output_count') AS INTEGER) AS after_rankllm,
            final_result_count AS final_returned,
            ROUND(try_cast(json_extract(payload_json, '$.phase_timings."search.plan"') AS DOUBLE) / 1000.0, 2) AS plan_s,
            ROUND(try_cast(json_extract(payload_json, '$.phase_timings."search.retrieve"') AS DOUBLE) / 1000.0, 2) AS retrieve_s,
            ROUND(try_cast(json_extract(payload_json, '$.phase_timings."search.rank"') AS DOUBLE) / 1000.0, 2) AS rank_s
        FROM search_runs
        ORDER BY recorded_at DESC
        """,
        # 22. Daily provider reliability
        f"""
        CREATE OR REPLACE VIEW {t}.vw_provider_reliability_daily AS
        SELECT
            CAST(recorded_at AS DATE) AS day,
            provider,
            COUNT(*) AS total_calls,
            COUNT(*) FILTER (WHERE status = 'success') AS success_calls,
            ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'success') / NULLIF(COUNT(*), 0), 1) AS success_rate_pct,
            COUNT(*) FILTER (WHERE result_class = 'timeout' OR error_type = 'TimeoutError') AS timeout_calls,
            COUNT(*) FILTER (WHERE status = 'incomplete') AS incomplete_calls,
            ROUND(quantile_cont(latency_ms, 0.50), 0) AS p50_latency_ms,
            ROUND(quantile_cont(latency_ms, 0.95), 0) AS p95_latency_ms
        FROM provider_calls
        GROUP BY 1, 2
        ORDER BY day DESC, total_calls DESC
        """,
        # 23. Rewrite attribution by branch role
        f"""
        CREATE OR REPLACE VIEW {t}.vw_rewrite_attribution AS
        SELECT
            sb.branch_role,
            COUNT(DISTINCT sb.run_key) AS total_runs,
            ROUND(AVG(sb.results_count), 2) AS avg_candidates_retrieved,
            ROUND(AVG(sb.latency_ms) / 1000.0, 2) AS avg_branch_latency_s
        FROM search_branches sb
        GROUP BY sb.branch_role
        ORDER BY total_runs DESC
        """,
        # 24. Cross-tool coverage
        f"""
        CREATE OR REPLACE VIEW {t}.vw_tool_call_coverage AS
        SELECT
            tool_name,
            COUNT(*) AS total_events,
            COUNT(*) FILTER (WHERE phase = 'request') AS request_events,
            COUNT(*) FILTER (WHERE phase = 'response') AS response_events,
            COUNT(*) FILTER (WHERE phase = 'error') AS error_events,
            COUNT(DISTINCT tool_call_id) AS distinct_tool_calls,
            COUNT(DISTINCT tool_call_id) FILTER (WHERE phase = 'request')
                - COUNT(DISTINCT tool_call_id) FILTER (WHERE phase IN ('response', 'error')) AS request_only_calls,
            ROUND(100.0 * COUNT(*) FILTER (WHERE phase IN ('response', 'error')) / NULLIF(COUNT(*) FILTER (WHERE phase = 'request'), 0), 2) AS terminal_event_rate_pct,
            ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95) FILTER (WHERE duration_ms IS NOT NULL), 2) AS p95_duration_ms,
            MIN(recorded_at) AS first_observed_at,
            MAX(recorded_at) AS last_observed_at
        FROM tool_calls
        GROUP BY tool_name
        ORDER BY total_events DESC
        """,
        # 25. Cross-tool linkage gaps
        f"""
        CREATE OR REPLACE VIEW {t}.vw_tool_call_linkage_gaps AS
        SELECT
            tool_name,
            COUNT(*) AS total_events,
            COUNT(DISTINCT tool_call_id) AS distinct_tool_call_ids,
            COUNT(*) FILTER (WHERE tool_call_id IS NULL) AS null_tool_call_id_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE tool_call_id IS NULL) / NULLIF(COUNT(*), 0), 2) AS null_tool_call_id_pct,
            COUNT(DISTINCT trace_id) AS distinct_trace_ids,
            COUNT(*) FILTER (WHERE trace_id IS NULL) AS null_trace_id_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE trace_id IS NULL) / NULLIF(COUNT(*), 0), 2) AS null_trace_id_pct,
            COUNT(DISTINCT session_id) AS distinct_session_ids,
            COUNT(*) FILTER (WHERE session_id IS NULL) AS null_session_id_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE session_id IS NULL) / NULLIF(COUNT(*), 0), 2) AS null_session_id_pct,
            COUNT(DISTINCT request_fingerprint) AS distinct_fingerprints,
            COUNT(*) FILTER (WHERE request_fingerprint IS NULL) AS null_fingerprint_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE request_fingerprint IS NULL) / NULLIF(COUNT(*), 0), 2) AS null_fingerprint_pct
        FROM tool_calls
        GROUP BY tool_name
        ORDER BY total_events DESC
        """,
        # 26. Web search tool linkage
        f"""
        CREATE OR REPLACE VIEW {t}.vw_web_search_tool_linkage AS
        SELECT
            tc.event_id,
            tc.recorded_at AS event_recorded_at,
            tc.tool_call_id,
            tc.session_id AS tool_session_id,
            tc.trace_id,
            tc.phase,
            tc.status AS tool_status,
            tc.duration_ms AS tool_duration_ms,
            sr.run_key,
            sr.session_id AS run_session_id,
            sr.query AS run_query,
            sr.status AS run_status,
            sr.duration_ms AS run_duration_ms,
            sr.final_result_count,
            CASE WHEN sr.run_key IS NOT NULL THEN TRUE ELSE FALSE END AS matched_to_search_run
        FROM tool_calls tc
        LEFT JOIN search_runs sr ON tc.tool_call_id = sr.tool_call_id
        WHERE tc.tool_name = 'web_search'
        ORDER BY tc.recorded_at DESC
        """,
        # 27. Quick web search performance
        f"""
        CREATE OR REPLACE VIEW {t}.vw_quick_web_search_performance AS
        SELECT
            COALESCE(client_model, 'unspecified') AS client_model,
            status,
            COUNT(*) AS total_runs,
            ROUND(AVG(total_citations), 2) AS avg_citations,
            SUM(total_citations) AS total_citations,
            COUNT(*) FILTER (WHERE warnings IS NOT NULL AND json_array_length(warnings) > 0) AS runs_with_warnings,
            COUNT(*) FILTER (WHERE usage IS NOT NULL) AS runs_with_usage,
            ROUND(AVG(duration_ms), 2) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.50), 2) AS p50_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95), 2) AS p95_duration_ms,
            COUNT(*) FILTER (WHERE status = 'success') AS success_count,
            COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count
        FROM quick_web_search_runs
        GROUP BY client_model, status
        ORDER BY total_runs DESC
        """,
        # 28. Quick web search citation sources
        f"""
        CREATE OR REPLACE VIEW {t}.vw_quick_web_search_citation_sources AS
        WITH extracted AS (
            SELECT
                terminal_event_id,
                citation_index,
                title,
                url,
                publish_date,
                CASE
                    WHEN url LIKE 'http://%' OR url LIKE 'https://%'
                    THEN regexp_extract(url, '^https?://([^/]+)', 1)
                    ELSE 'other'
                END AS domain
            FROM quick_web_search_citations
        )
        SELECT
            domain,
            COUNT(*) AS citation_count,
            COUNT(DISTINCT terminal_event_id) AS distinct_runs,
            COUNT(*) FILTER (WHERE publish_date IS NOT NULL AND publish_date <> '') AS citations_with_date,
            ROUND(100.0 * COUNT(*) FILTER (WHERE publish_date IS NOT NULL AND publish_date <> '') / NULLIF(COUNT(*), 0), 2) AS date_presence_pct
        FROM extracted
        GROUP BY domain
        ORDER BY citation_count DESC
        """,
        # 29. Gemini search performance
        f"""
        CREATE OR REPLACE VIEW {t}.vw_gemini_search_performance AS
        SELECT
            COALESCE(model_used, 'unknown') AS model_used,
            COALESCE(mode, 'standard') AS mode,
            status,
            COUNT(*) AS total_runs,
            COUNT(*) FILTER (WHERE answer IS NOT NULL AND LENGTH(answer) > 0) AS answer_present_count,
            ROUND(AVG(grounding_chunks_count), 2) AS avg_grounding_chunks,
            ROUND(AVG(web_search_queries_count), 2) AS avg_web_search_queries,
            ROUND(AVG(prompt_tokens), 1) AS avg_prompt_tokens,
            ROUND(AVG(completion_tokens), 1) AS avg_completion_tokens,
            ROUND(AVG(total_tokens), 1) AS avg_total_tokens,
            COUNT(*) FILTER (WHERE fallback_chain IS NOT NULL AND len(fallback_chain) > 0) AS fallback_activated_count,
            ROUND(AVG(duration_ms), 2) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.50), 2) AS p50_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95), 2) AS p95_duration_ms
        FROM gemini_search_runs
        GROUP BY model_used, mode, status
        ORDER BY total_runs DESC
        """,
        # 30. Gemini search fallbacks
        f"""
        CREATE OR REPLACE VIEW {t}.vw_gemini_search_fallbacks AS
        SELECT
            gsr.terminal_event_id,
            gsr.recorded_at,
            gsr.query,
            gsr.model_used AS final_model_used,
            gsr.fallback_chain,
            gsr.fallback_reason,
            CASE
                WHEN gsr.fallback_chain IS NOT NULL AND len(gsr.fallback_chain) > 0 THEN TRUE
                ELSE FALSE
            END AS fallback_occurred,
            (SELECT COUNT(*) FROM gemini_search_attempts gsa WHERE gsa.tool_call_id = gsr.tool_call_id) AS recorded_attempt_count,
            'incomplete_attempt_coverage_pre_instrumentation' AS attempt_coverage_status
        FROM gemini_search_runs gsr
        ORDER BY gsr.recorded_at DESC
        """,
        # 31. Gemini search sources
        f"""
        CREATE OR REPLACE VIEW {t}.vw_gemini_search_sources AS
        WITH extracted AS (
            SELECT
                terminal_event_id,
                source_kind,
                source_index,
                url,
                title,
                CASE
                    WHEN url LIKE 'http://%' OR url LIKE 'https://%'
                    THEN regexp_extract(url, '^https?://([^/]+)', 1)
                    ELSE 'other'
                END AS domain
            FROM gemini_search_sources
        )
        SELECT
            source_kind,
            domain,
            COUNT(*) AS total_sources,
            COUNT(DISTINCT terminal_event_id) AS distinct_runs
        FROM extracted
        GROUP BY source_kind, domain
        ORDER BY total_sources DESC
        """,
        # 32. Code search provider yield
        f"""
        CREATE OR REPLACE VIEW {t}.vw_code_search_provider_yield AS
        SELECT
            provider,
            outcome,
            COUNT(*) AS total_responses,
            SUM(hit_count) AS total_hits_returned,
            ROUND(AVG(hit_count), 2) AS avg_hits_per_response,
            SUM(request_count) AS total_requests,
            ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95) FILTER (WHERE duration_ms IS NOT NULL), 2) AS p95_duration_ms,
            COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count
        FROM code_search_providers
        GROUP BY provider, outcome
        ORDER BY total_responses DESC, provider
        """,
        # 33. Code search hit sources
        f"""
        CREATE OR REPLACE VIEW {t}.vw_code_search_hit_sources AS
        SELECT
            COALESCE(provider, 'unknown') AS provider,
            COALESCE(result_kind, 'unknown') AS result_kind,
            COALESCE(evidence_role, 'unknown') AS evidence_role,
            COUNT(*) AS total_hits,
            COUNT(*) FILTER (WHERE hydrated = TRUE) AS hydrated_hits,
            COUNT(*) FILTER (WHERE lines_available = TRUE) AS lines_available_hits,
            COUNT(*) FILTER (WHERE match_data_available = TRUE) AS match_data_available_hits,
            ROUND(AVG(final_score), 4) AS avg_final_score,
            ROUND(AVG(fragment_count), 2) AS avg_fragments,
            ROUND(AVG(symbol_count), 2) AS avg_symbols
        FROM code_search_hits
        GROUP BY provider, result_kind, evidence_role
        ORDER BY total_hits DESC
        """,
        # 34. Code search variant effectiveness
        f"""
        CREATE OR REPLACE VIEW {t}.vw_code_search_variant_effectiveness AS
        SELECT
            cshv.query_variant,
            cshv.provider,
            COUNT(DISTINCT cshv.terminal_event_id) AS runs_with_variant_hit,
            COUNT(*) AS total_associated_hits,
            COUNT(*) FILTER (WHERE cshv.hit_rank = 1) AS rank1_hits,
            COUNT(*) FILTER (WHERE cshv.hit_rank <= 3) AS top3_hits,
            ROUND(AVG(csh.final_score), 4) AS avg_hit_final_score,
            COUNT(DISTINCT csh.repository) AS distinct_repositories
        FROM code_search_hit_variants cshv
        LEFT JOIN code_search_hits csh ON cshv.terminal_event_id = csh.terminal_event_id AND cshv.hit_rank = csh.hit_rank
        GROUP BY cshv.query_variant, cshv.provider
        ORDER BY total_associated_hits DESC
        """,
        # 35. Code search rerank execution
        f"""
        CREATE OR REPLACE VIEW {t}.vw_code_search_rerank_execution AS
        SELECT
            COALESCE(provider, 'unspecified') AS provider,
            COALESCE(model, 'unspecified') AS model,
            status,
            diagnostic_outcome,
            COUNT(*) AS total_executions,
            SUM(input_count) AS total_input_hits,
            SUM(output_count) AS total_output_hits,
            SUM(reranked_count) AS total_reranked_hits,
            ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95) FILTER (WHERE duration_ms IS NOT NULL), 2) AS p95_duration_ms
        FROM code_search_rerank
        GROUP BY provider, model, status, diagnostic_outcome
        ORDER BY total_executions DESC
        """,
        # 36. Code search diagnostic patterns
        f"""
        CREATE OR REPLACE VIEW {t}.vw_code_search_diagnostic_patterns AS
        SELECT
            COALESCE(provider, 'all') AS provider,
            outcome,
            failure_kind,
            message,
            status_code,
            COUNT(*) AS diagnostic_count,
            COUNT(DISTINCT terminal_event_id) AS affected_runs
        FROM code_search_diagnostics
        GROUP BY provider, outcome, failure_kind, message, status_code
        ORDER BY diagnostic_count DESC
        """,
        # 37. Code search repository discovery
        f"""
        CREATE OR REPLACE VIEW {t}.vw_code_search_repository_discovery AS
        SELECT
            COALESCE(language, 'unknown') AS language,
            verified,
            archived,
            fork,
            COUNT(*) AS discovered_repo_count,
            ROUND(AVG(stars), 1) AS avg_stars,
            ROUND(AVG(forks), 1) AS avg_forks,
            ROUND(AVG(proof_hits), 2) AS avg_proof_hits,
            ROUND(AVG(discovery_score) FILTER (WHERE discovery_score IS NOT NULL), 4) AS avg_discovery_score
        FROM code_search_repositories
        GROUP BY language, verified, archived, fork
        ORDER BY discovered_repo_count DESC
        """,
        # 38. Code search score component distribution
        f"""
        CREATE OR REPLACE VIEW {t}.vw_code_search_score_component_distribution AS
        SELECT
            COALESCE(provider, 'unknown') AS provider,
            COALESCE(result_kind, 'unknown') AS result_kind,
            COALESCE(evidence_role, 'unknown') AS evidence_role,
            COUNT(*) AS hit_count,
            ROUND(AVG(final_score), 4) AS avg_final_score,
            ROUND(AVG(TRY_CAST(json_extract(score_components, '$.exact_symbol') AS DOUBLE)), 4) AS avg_exact_symbol_score,
            ROUND(AVG(TRY_CAST(json_extract(score_components, '$.symbol_match') AS DOUBLE)), 4) AS avg_symbol_match_score,
            ROUND(AVG(TRY_CAST(json_extract(score_components, '$.path_relevance') AS DOUBLE)), 4) AS avg_path_relevance_score,
            ROUND(AVG(TRY_CAST(json_extract(score_components, '$.anchor_match') AS DOUBLE)), 4) AS avg_anchor_match_score,
            ROUND(AVG(TRY_CAST(json_extract(score_components, '$.discovery_score') AS DOUBLE)), 4) AS avg_discovery_score
        FROM code_search_hits
        GROUP BY provider, result_kind, evidence_role
        ORDER BY hit_count DESC
        """,
        # 39. Content fetch performance
        f"""
        CREATE OR REPLACE VIEW {t}.vw_content_fetch_performance AS
        SELECT
            co.tool_name,
            cf.fetch_backend,
            cf.source_type,
            cf.status AS fetch_status,
            COUNT(*) AS total_fetches,
            COUNT(DISTINCT co.terminal_event_id) AS distinct_operations,
            ROUND(AVG(cf.content_length), 0) AS avg_content_length,
            ROUND(AVG(cf.page_char_count), 0) AS avg_page_char_count,
            ROUND(AVG(cf.word_count), 0) AS avg_word_count,
            COUNT(*) FILTER (WHERE cf.window_has_more = TRUE) AS truncated_windows_count,
            ROUND(AVG(cf.item_duration_ms) FILTER (WHERE cf.item_duration_ms IS NOT NULL), 2) AS avg_item_duration_ms,
            ROUND(AVG(co.duration_ms), 2) AS avg_parent_operation_duration_ms
        FROM content_operations co
        JOIN content_fetches cf ON co.terminal_event_id = cf.terminal_event_id
        GROUP BY co.tool_name, cf.fetch_backend, cf.source_type, cf.status
        ORDER BY total_fetches DESC
        """,
        # 40. Content summary output signals
        f"""
        CREATE OR REPLACE VIEW {t}.vw_content_summary_output_signals AS
        SELECT
            COALESCE(backend, 'unspecified') AS backend,
            COALESCE(model_used, 'unspecified') AS model_used,
            is_batch,
            is_stub,
            status,
            COUNT(*) AS total_summaries,
            ROUND(AVG(summary_length_chars), 0) AS avg_summary_chars,
            ROUND(AVG(key_points_count), 2) AS avg_key_points,
            ROUND(AVG(important_entities_count), 2) AS avg_entities,
            ROUND(AVG(verbatim_terms_count), 2) AS avg_verbatim_terms,
            ROUND(AVG(limitations_count), 2) AS avg_limitations,
            COUNT(*) FILTER (WHERE source_date IS NOT NULL AND source_date <> '') AS summaries_with_source_date,
            ROUND(AVG(input_tokens) FILTER (WHERE input_tokens IS NOT NULL), 1) AS avg_input_tokens,
            ROUND(AVG(output_tokens) FILTER (WHERE output_tokens IS NOT NULL), 1) AS avg_output_tokens,
            ROUND(AVG(total_tokens) FILTER (WHERE total_tokens IS NOT NULL), 1) AS avg_total_tokens,
            ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms
        FROM content_summaries
        GROUP BY backend, model_used, is_batch, is_stub, status
        ORDER BY total_summaries DESC
        """,
        # 41. Content summary attempt performance
        f"""
        CREATE OR REPLACE VIEW {t}.vw_content_summary_attempt_performance AS
        SELECT
            COALESCE(backend, 'unspecified') AS backend,
            COALESCE(model_used, 'unspecified') AS model_used,
            is_batch,
            status,
            COUNT(*) AS total_attempts,
            ROUND(AVG(input_chars) FILTER (WHERE input_chars IS NOT NULL), 0) AS avg_input_chars,
            ROUND(AVG(input_tokens) FILTER (WHERE input_tokens IS NOT NULL), 1) AS avg_input_tokens,
            ROUND(AVG(output_tokens) FILTER (WHERE output_tokens IS NOT NULL), 1) AS avg_output_tokens,
            ROUND(AVG(total_tokens) FILTER (WHERE total_tokens IS NOT NULL), 1) AS avg_total_tokens,
            ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms,
            ROUND(quantile_cont(duration_ms, 0.95) FILTER (WHERE duration_ms IS NOT NULL), 2) AS p95_duration_ms,
            COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count
        FROM content_summary_attempts
        GROUP BY backend, model_used, is_batch, status
        ORDER BY total_attempts DESC
        """,
        # 42. Content summary batch vs single
        f"""
        CREATE OR REPLACE VIEW {t}.vw_content_summary_batch_vs_single AS
        SELECT
            co.tool_name,
            cs.is_batch,
            COUNT(DISTINCT co.terminal_event_id) AS total_operations,
            COUNT(cs.item_index) AS total_summary_items,
            ROUND(AVG(co.input_count), 2) AS avg_items_per_operation,
            ROUND(AVG(co.duration_ms), 2) AS avg_operation_duration_ms,
            ROUND(AVG(cs.summary_length_chars), 0) AS avg_item_summary_chars,
            ROUND(AVG(cs.key_points_count), 2) AS avg_item_key_points,
            COUNT(*) FILTER (WHERE cs.is_stub = TRUE) AS stub_items_count
        FROM content_operations co
        LEFT JOIN content_summaries cs ON co.terminal_event_id = cs.terminal_event_id
        GROUP BY co.tool_name, cs.is_batch
        ORDER BY total_operations DESC
        """,
        # 43. Content summary fallbacks
        f"""
        CREATE OR REPLACE VIEW {t}.vw_content_summary_fallbacks AS
        SELECT
            cs.terminal_event_id,
            cs.recorded_at,
            cs.normalized_url,
            cs.backend,
            cs.model_used,
            cs.fallback_attempted,
            cs.fallback_tier,
            cs.is_stub,
            cs.status,
            (SELECT COUNT(*) FROM content_summary_attempts csa WHERE csa.tool_call_id = cs.tool_call_id) AS recorded_attempt_count,
            CASE
                WHEN cs.fallback_attempted = TRUE OR (cs.fallback_tier IS NOT NULL AND cs.fallback_tier > 0) THEN TRUE
                ELSE FALSE
            END AS fallback_indicated
        FROM content_summaries cs
        ORDER BY cs.recorded_at DESC
        """,
        # 44. Content summary focus comparison
        f"""
        CREATE OR REPLACE VIEW {t}.vw_content_summary_focus_comparison AS
        SELECT
            CASE WHEN focus_query IS NOT NULL AND focus_query <> '' THEN 'focused' ELSE 'unfocused' END AS focus_mode,
            is_batch,
            COUNT(*) AS total_summaries,
            ROUND(AVG(summary_length_chars), 0) AS avg_summary_chars,
            ROUND(AVG(key_points_count), 2) AS avg_key_points,
            ROUND(AVG(important_entities_count), 2) AS avg_important_entities,
            ROUND(AVG(verbatim_terms_count), 2) AS avg_verbatim_terms,
            ROUND(AVG(limitations_count), 2) AS avg_limitations,
            ROUND(AVG(input_chars) FILTER (WHERE input_chars IS NOT NULL), 0) AS avg_input_chars
        FROM content_summaries
        GROUP BY 1, is_batch
        ORDER BY focus_mode, is_batch
        """,
        # 45. Content summary daily tokens
        f"""
        CREATE OR REPLACE VIEW {t}.vw_content_summary_daily_tokens AS
        SELECT
            CAST(recorded_at AS DATE) AS day,
            COALESCE(backend, 'unspecified') AS backend,
            COALESCE(model_used, 'unspecified') AS model_used,
            is_batch,
            COUNT(*) AS total_summaries,
            SUM(input_tokens) AS known_input_tokens,
            SUM(output_tokens) AS known_output_tokens,
            SUM(total_tokens) AS known_total_tokens,
            COUNT(*) FILTER (WHERE input_tokens IS NOT NULL) AS summaries_with_token_data
        FROM content_summaries
        GROUP BY 1, 2, 3, 4
        ORDER BY day DESC, total_summaries DESC
        """,
    ]


def _build_funnel_uplift_view_sql(target: str) -> list[str]:
    """Return SQL for the 8 web-search funnel uplift analytical views."""
    t = target
    return [
        # 46. Run stage funnel — synthetic stages + actual rerank stages
        f"""
        CREATE OR REPLACE VIEW {t}.vw_run_stage_funnel AS
        WITH provider_counts AS (
            SELECT
                run_key,
                count(*)::INTEGER AS provider_raw,
                count(DISTINCT canonical_result_id)::INTEGER AS provider_unique,
                count(DISTINCT canonical_result_id) FILTER (WHERE is_eligible)::INTEGER AS eligible
            FROM provider_results
            GROUP BY run_key
        ), merge_counts AS (
            SELECT run_key, count(*)::INTEGER AS merged
            FROM search_candidates
            GROUP BY run_key
        ), final_counts AS (
            SELECT run_key, count(*)::INTEGER AS final_count
            FROM final_results
            GROUP BY run_key
        ), per_run AS (
            SELECT
                r.run_key,
                r.status AS run_status,
                coalesce(p.provider_raw, 0) AS provider_raw,
                coalesce(p.provider_unique, 0) AS provider_unique,
                coalesce(p.eligible, 0) AS eligible,
                coalesce(m.merged, 0) AS merged,
                coalesce(f.final_count, 0) AS final_count
            FROM search_runs r
            LEFT JOIN provider_counts p USING (run_key)
            LEFT JOIN merge_counts m USING (run_key)
            LEFT JOIN final_counts f USING (run_key)
        ), synthetic AS (
            SELECT run_key, -40 AS stage_order, 'provider_raw' AS stage_name,
                   'success' AS status, provider_raw AS input_count, provider_raw AS output_count
            FROM per_run
            UNION ALL
            SELECT run_key, -30, 'provider_unique', 'success', provider_raw, provider_unique
            FROM per_run
            UNION ALL
            SELECT run_key, -20, 'eligible', 'success', provider_unique, eligible
            FROM per_run
            UNION ALL
            SELECT run_key, -10, 'merge', 'success', eligible, merged
            FROM per_run
            UNION ALL
            SELECT run_key, 1000, 'final',
                   CASE WHEN run_status = 'running' THEN 'pending' ELSE run_status END,
                   merged, final_count
            FROM per_run
        ), stages AS (
            SELECT
                run_key,
                CASE stage
                    WHEN 'bi_encoder' THEN 100
                    WHEN 'cross_encoder' THEN 200
                    WHEN 'rankllm' THEN 300
                    ELSE 999
                END AS stage_order,
                stage AS stage_name,
                status,
                input_count,
                output_count
            FROM rerank_stages
        )
        SELECT * FROM synthetic
        UNION ALL
        SELECT * FROM stages
        """,
        # 47. Run funnel — pivoted one-row-per-run funnel
        f"""
        CREATE OR REPLACE VIEW {t}.vw_run_funnel AS
        SELECT
            run_key,
            max(output_count) FILTER (WHERE stage_name = 'provider_raw') AS provider_raw,
            max(output_count) FILTER (WHERE stage_name = 'provider_unique') AS provider_unique,
            max(output_count) FILTER (WHERE stage_name = 'eligible') AS eligible,
            max(output_count) FILTER (WHERE stage_name = 'merge') AS merged,
            max(output_count) FILTER (WHERE stage_name = 'bi_encoder') AS bi_output,
            max(status) FILTER (WHERE stage_name = 'bi_encoder') AS bi_status,
            max(output_count) FILTER (WHERE stage_name = 'cross_encoder') AS cross_output,
            max(status) FILTER (WHERE stage_name = 'cross_encoder') AS cross_status,
            max(output_count) FILTER (WHERE stage_name = 'rankllm') AS rankllm_output,
            max(status) FILTER (WHERE stage_name = 'rankllm') AS rankllm_status,
            max(output_count) FILTER (WHERE stage_name = 'final') AS final_count
        FROM {t}.vw_run_stage_funnel
        GROUP BY run_key
        """,
        # 48. Candidate trajectory — per-candidate journey from discovery to final
        f"""
        CREATE OR REPLACE VIEW {t}.vw_candidate_trajectory AS
        WITH discovery AS (
            SELECT
                run_key,
                canonical_result_id,
                count(DISTINCT provider) AS discovering_providers,
                count(DISTINCT branch_id) AS discovering_branches,
                min(provider_rank) AS best_provider_rank
            FROM provider_results
            WHERE is_eligible
            GROUP BY run_key, canonical_result_id
        )
        SELECT
            c.run_key,
            c.canonical_result_id,
            c.link AS canonical_url,
            d.discovering_providers,
            d.discovering_branches,
            d.best_provider_rank,
            c.rrf_score,
            f.rank AS final_rank,
            f.final_score
        FROM search_candidates c
        LEFT JOIN discovery d USING (run_key, canonical_result_id)
        LEFT JOIN final_results f USING (run_key, canonical_result_id)
        """,
        # 49. Provider contribution — fractional discovery credit per provider
        f"""
        CREATE OR REPLACE VIEW {t}.vw_provider_contribution AS
        WITH discovered AS (
            SELECT DISTINCT run_key, provider, canonical_result_id
            FROM provider_results
        ), multiplicity AS (
            SELECT run_key, canonical_result_id, count(*) AS provider_count
            FROM discovered
            GROUP BY ALL
        ), eligible AS (
            SELECT DISTINCT run_key, provider, canonical_result_id
            FROM provider_results
            WHERE is_eligible
        )
        SELECT
            d.run_key,
            d.provider,
            count(*) AS discovered_unique,
            count(*) FILTER (WHERE m.provider_count = 1) AS exclusive_candidates,
            sum(1.0 / m.provider_count) AS fractional_discovery_credit,
            count(e.canonical_result_id) AS eligible_candidates,
            count(c.canonical_result_id) AS merged_candidates,
            count(f.canonical_result_id) AS final_candidates
        FROM discovered d
        JOIN multiplicity m USING (run_key, canonical_result_id)
        LEFT JOIN eligible e USING (run_key, provider, canonical_result_id)
        LEFT JOIN search_candidates c USING (run_key, canonical_result_id)
        LEFT JOIN final_results f USING (run_key, canonical_result_id)
        GROUP BY d.run_key, d.provider
        """,
        # 50. Branch contribution — fractional discovery credit per branch
        f"""
        CREATE OR REPLACE VIEW {t}.vw_branch_contribution AS
        WITH discovered AS (
            SELECT DISTINCT run_key, branch_id, canonical_result_id
            FROM provider_results
        ), multiplicity AS (
            SELECT run_key, canonical_result_id, count(*) AS branch_count
            FROM discovered
            GROUP BY ALL
        ), eligible AS (
            SELECT DISTINCT run_key, branch_id, canonical_result_id
            FROM provider_results
            WHERE is_eligible
        )
        SELECT
            d.run_key,
            d.branch_id,
            count(*) AS discovered_unique,
            count(*) FILTER (WHERE m.branch_count = 1) AS exclusive_candidates,
            sum(1.0 / m.branch_count) AS fractional_discovery_credit,
            count(e.canonical_result_id) AS eligible_candidates,
            count(c.canonical_result_id) AS merged_candidates,
            count(f.canonical_result_id) AS final_candidates
        FROM discovered d
        JOIN multiplicity m USING (run_key, canonical_result_id)
        LEFT JOIN eligible e USING (run_key, branch_id, canonical_result_id)
        LEFT JOIN search_candidates c USING (run_key, canonical_result_id)
        LEFT JOIN final_results f USING (run_key, canonical_result_id)
        GROUP BY d.run_key, d.branch_id
        """,
        # 51. Rewrite value — ROI per query variant
        f"""
        CREATE OR REPLACE VIEW {t}.vw_rewrite_value AS
        SELECT
            v.run_key,
            v.variant_id,
            v.variant_role,
            v.query_text,
            v.selected,
            v.executed,
            count(DISTINCT b.branch_index) AS branches,
            coalesce(sum(bc.discovered_unique), 0) AS discovered_unique,
            coalesce(sum(bc.exclusive_candidates), 0) AS exclusive_candidates,
            coalesce(sum(bc.fractional_discovery_credit), 0) AS fractional_discovery_credit,
            coalesce(sum(bc.merged_candidates), 0) AS merged_candidates,
            coalesce(sum(bc.final_candidates), 0) AS final_candidates
        FROM query_variants v
        LEFT JOIN search_branches b ON b.run_key = v.run_key
        LEFT JOIN {t}.vw_branch_contribution bc
            ON bc.run_key = v.run_key AND bc.branch_id = b.branch_id
        GROUP BY ALL
        """,
        # 52. Followup attribution — search→content linkage
        f"""
        CREATE OR REPLACE VIEW {t}.vw_followup_attribution AS
        SELECT
            o.tool_call_id,
            o.run_key,
            o.tool_name,
            o.item_rank,
            o.canonical_result_id,
            o.raw_url,
            o.title,
            o.session_id,
            coalesce(fr.fetch_attempts, 0) AS fetch_attempts,
            coalesce(fr.successful_fetches, 0) AS successful_fetches
        FROM tool_output_items o
        LEFT JOIN (
            SELECT
                normalized_url,
                count(*) AS fetch_attempts,
                count(*) FILTER (WHERE status = 'success') AS successful_fetches
            FROM content_fetches
            GROUP BY normalized_url
        ) fr ON o.raw_url = fr.normalized_url
        WHERE o.tool_name = 'web_search'
        """,
        # 53. Result usefulness — judgment + fetch rollup per output item
        f"""
        CREATE OR REPLACE VIEW {t}.vw_result_usefulness AS
        SELECT
            o.output_item_id,
            o.tool_call_id,
            o.run_key,
            o.item_type,
            o.item_rank,
            o.canonical_result_id,
            o.raw_url,
            coalesce(fr.fetch_attempts, 0) AS fetch_attempts,
            coalesce(fr.successful_fetches, 0) AS successful_fetches,
            fr.max_content_chars
        FROM tool_output_items o
        LEFT JOIN (
            SELECT
                normalized_url,
                count(*) AS fetch_attempts,
                count(*) FILTER (WHERE status = 'success') AS successful_fetches,
                max(content_length) AS max_content_chars
            FROM content_fetches
            GROUP BY normalized_url
        ) fr ON o.raw_url = fr.normalized_url
        """,
        # 54. Dense score calibration — rerank score vs survival
        f"""
        CREATE OR REPLACE VIEW {t}.vw_dense_score_calibration AS
        SELECT
            stage AS stage_name,
            floor(score_after * 10) / 10 AS score_bin,
            count(*) AS candidates,
            avg((rank_after IS NOT NULL)::INTEGER) AS survival_rate,
            avg(score_after) AS avg_score_after
        FROM rerank_candidates
        WHERE score_after IS NOT NULL
        GROUP BY stage_name, score_bin
        """,
    ]


def ensure_views(*, db_path: str | None = None) -> None:
    """Create or replace all analytics views against the local DuckDB store."""
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    if not path.exists():
        return
    # Schema installation acquires the same process lock. Perform it before
    # taking the view-creation lock to avoid a non-reentrant lock deadlock.
    ensure_store_schema(db_path=db_path)
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
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
            # Funnel uplift views
            for statement in _build_funnel_uplift_view_sql("main"):
                connection.execute(statement)
        finally:
            connection.close()


def refresh_views(*, db_path: str | None = None) -> None:
    """Recreate all views (useful after schema migrations)."""
    ensure_views(db_path=db_path)


def refresh_materialized_summaries(*, db_path: str | None = None) -> None:
    """Rebuild materialized summary tables from current fact data."""
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    if not path.exists():
        return
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            connection.execute("""
                CREATE OR REPLACE TABLE summary_provider_discovery_daily AS
                SELECT
                    date_trunc('day', recorded_at)::DATE AS day,
                    provider,
                    COUNT(*) AS total_calls,
                    COUNT(DISTINCT run_key) AS distinct_runs,
                    COUNT(*) FILTER (WHERE status = 'error') AS error_count,
                    ROUND(AVG(latency_ms), 1) AS avg_latency_ms
                FROM provider_calls
                GROUP BY ALL
            """)
            connection.execute("""
                CREATE OR REPLACE TABLE summary_rewrite_value_daily AS
                SELECT
                    date_trunc('day', recorded_at)::DATE AS day,
                    variant_role,
                    COUNT(*) AS total_variants,
                    COUNT(*) FILTER (WHERE selected) AS selected_count,
                    COUNT(*) FILTER (WHERE executed) AS executed_count,
                    COUNT(*) FILTER (WHERE skip_reason IS NOT NULL) AS skipped_count
                FROM query_variants
                GROUP BY ALL
            """)
        finally:
            connection.close()


ensure_local_views = ensure_views


def build_analytics_view_sql(schema: str) -> list[str]:
    """Return SQL statements to create analytics views in a remote schema."""
    return [
        *_build_dashboard_view_sql(schema),
        *build_eval_view_sql(schema),
    ]
