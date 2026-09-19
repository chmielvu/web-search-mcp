"""SQL for the human-readable dashboard views.

Builds the run/provider/branch/candidate views the dashboard reads, plus the
quality-diagnostic, judge, and legacy-judge views: ``vw_run_summary``,
``vw_provider_performance``, ``vw_branch_summary``, ``vw_candidate_funnel``,
``vw_rerank_timeline``, ``vw_rewrite_diagnostics``, ``vw_daily_trend``,
``vw_quality_distribution``, ``vw_judge_quality``, ``vw_llm_judgments``,
``vw_flockmtl_resources``, ``vw_judge_facet_agg``, ``vw_legacy_judge_quality``,
``vw_result_quality_diagnostics`` and their companion statements.

Placeholder ``{t}`` in each statement is the target schema/prefix (``main`` for
the local store, a remote schema for synced databases).
"""


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
            (SELECT rc.cross_encoder_score FROM rerank_candidates rc
             WHERE rc.run_key = c.run_key AND rc.link = c.link AND rc.stage = 'cross_encoder'
             LIMIT 1) AS cross_encoder_score,
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
                WHEN rs.stage = 'mmr_fallback' THEN '4. MMR Fallback'
                ELSE rs.stage
            END AS stage_label,
            rs.provider, rs.model,
            rs.input_count, rs.output_count,
            ROUND(100.0 * rs.output_count / NULLIF(rs.input_count, 0), 1) AS survival_rate_pct,
            ROUND(rs.duration_ms, 1) AS duration_ms,
            ROUND(rs.max_score, 4) AS max_score,
            ROUND(rs.avg_score, 4) AS avg_score,
            rs.status, rs.error_type,
            rs.input_tokens, rs.output_tokens,
            rs.attempted_passes, rs.valid_passes, rs.failed_passes
        FROM rerank_stages rs
        WHERE rs.stage IN ('bi_encoder', 'cross_encoder', 'rankllm', 'mmr_fallback')
        ORDER BY rs.run_key,
            CASE
                WHEN rs.stage = 'bi_encoder' THEN 1
                WHEN rs.stage = 'cross_encoder' THEN 2
                WHEN rs.stage = 'rankllm' THEN 3
                WHEN rs.stage = 'mmr_fallback' THEN 4
                ELSE 5
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
        # NOT burn LLM provider credits — the verdicts are persisted.
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
