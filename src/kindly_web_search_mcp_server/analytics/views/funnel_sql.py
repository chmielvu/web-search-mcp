"""SQL for the funnel-uplift views.

Stage-by-stage conversion and contribution analysis: ``vw_run_stage_funnel``,
``vw_run_funnel``, ``vw_candidate_trajectory``, ``vw_provider_contribution``,
``vw_branch_contribution``, ``vw_rewrite_value``, ``vw_followup_attribution``,
``vw_result_usefulness``, and ``vw_dense_score_calibration``.

Placeholder ``{t}`` in each statement is the target schema/prefix.
"""


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
                    WHEN 'mmr_fallback' THEN 400
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
            max(output_count) FILTER (WHERE stage_name = 'mmr_fallback') AS mmr_output,
            max(status) FILTER (WHERE stage_name = 'mmr_fallback') AS mmr_status,
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
        LEFT JOIN search_branches b
            ON b.run_key = v.run_key AND b.branch_id = v.branch_id
        LEFT JOIN {t}.vw_branch_contribution bc
            ON bc.run_key = v.run_key AND bc.branch_id = v.branch_id
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
        # 54. Final score calibration — rerank score vs survival
        f"""
        CREATE OR REPLACE VIEW {t}.vw_dense_score_calibration AS
        SELECT
            stage AS stage_name,
            floor(final_score_after * 10) / 10 AS final_score_bin,
            count(*) AS candidates,
            avg((rank_after IS NOT NULL)::INTEGER) AS survival_rate,
            avg(final_score_after) AS avg_final_score_after
        FROM rerank_candidates
        WHERE final_score_after IS NOT NULL
        GROUP BY stage_name, final_score_bin
        """,
    ]
