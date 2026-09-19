"""Views for normalized adaptive web-search analytics."""

from __future__ import annotations


def _build_adaptive_view_sql(schema: str) -> list[str]:
    """Build adaptive-search analyst views for a local or remote schema."""
    return [
        f"""
        CREATE OR REPLACE VIEW {schema}.vw_adaptive_search_run AS
        WITH adaptive_llm AS (
            SELECT
                run_key,
                COUNT(*) AS adaptive_llm_calls,
                COUNT(*) FILTER (
                    WHERE call_purpose = 'search.adaptive.followup'
                ) AS followup_llm_calls,
                COUNT(*) FILTER (
                    WHERE call_purpose = 'search.adaptive.decision'
                ) AS decision_llm_calls,
                COUNT(*) FILTER (
                    WHERE call_purpose = 'search.adaptive.synthesis'
                ) AS synthesis_llm_calls,
                SUM(input_tokens) AS adaptive_input_tokens,
                SUM(output_tokens) AS adaptive_output_tokens,
                SUM(tokens_used) AS adaptive_tokens_used,
                SUM(duration_ms) AS adaptive_duration_ms,
                SUM(cost_usd) AS adaptive_cost_usd
            FROM {schema}.llm_call_log
            WHERE call_purpose IN (
                'search.adaptive.followup',
                'search.adaptive.decision',
                'search.adaptive.synthesis'
            )
            GROUP BY run_key
        ), latest_adaptive_runs AS (
            SELECT *
            FROM {schema}.adaptive_search_runs
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY run_key
                ORDER BY recorded_at DESC
            ) = 1
        )
        SELECT
            a.run_key,
            a.recorded_at AS adaptive_recorded_at,
            r.recorded_at AS search_recorded_at,
            r.tool_call_id,
            r.session_id,
            r.query,
            r.normalized_query,
            r.research_goal,
            r.intent,
            r.status,
            r.error_type,
            r.duration_ms,
            r.branch_count,
            r.provider_count,
            r.merged_count,
            r.reranked_count,
            r.final_result_count,
            r.candidate_count,
            r.reranker_provider,
            r.reranker_model,
            a.rounds,
            a.stop_reason,
            a.prompt_version,
            a.synthesis,
            l.adaptive_llm_calls,
            l.followup_llm_calls,
            l.decision_llm_calls,
            l.synthesis_llm_calls,
            l.adaptive_input_tokens,
            l.adaptive_output_tokens,
            l.adaptive_tokens_used,
            l.adaptive_duration_ms,
            l.adaptive_cost_usd
        FROM latest_adaptive_runs a
        JOIN {schema}.search_runs r USING (run_key)
        LEFT JOIN adaptive_llm l USING (run_key)
        """,
        f"""
        CREATE OR REPLACE VIEW {schema}.vw_adaptive_search_round AS
        WITH round_branches AS (
            SELECT
                r.run_key,
                r.round_index,
                b.branch_index,
                b.branch_id
            FROM {schema}.adaptive_search_rounds r
            JOIN {schema}.search_branches b
                ON b.run_key = r.run_key
               AND b.branch_index >= r.branch_start
               AND b.branch_index < r.branch_start + r.branch_count
        ), branch_counts AS (
            SELECT
                run_key,
                round_index,
                COUNT(DISTINCT branch_index) AS persisted_branch_rows
            FROM round_branches
            GROUP BY run_key, round_index
        ), provider_counts AS (
            SELECT
                rb.run_key,
                rb.round_index,
                COUNT(*) AS provider_call_rows,
                COUNT(*) FILTER (WHERE pc.status = 'error') AS provider_error_rows,
                COUNT(*) FILTER (WHERE pc.status = 'incomplete') AS provider_incomplete_rows
            FROM round_branches rb
            JOIN {schema}.provider_calls pc
                ON pc.run_key = rb.run_key
               AND pc.branch_index = rb.branch_index
            GROUP BY rb.run_key, rb.round_index
        ), result_counts AS (
            SELECT
                rb.run_key,
                rb.round_index,
                COUNT(*) AS provider_result_rows,
                COUNT(*) FILTER (WHERE pr.is_eligible) AS eligible_result_rows
            FROM round_branches rb
            JOIN {schema}.provider_results pr
                ON pr.run_key = rb.run_key
               AND pr.branch_id = rb.branch_id
            GROUP BY rb.run_key, rb.round_index
        )
        SELECT
            r.run_key,
            r.round_index,
            r.branch_start,
            r.branch_count,
            r.queries,
            r.candidate_count,
            r.new_url_count,
            r.domain_count,
            r.provider_failure_count,
            r.decision,
            r.reason,
            COALESCE(b.persisted_branch_rows, 0) AS persisted_branch_rows,
            COALESCE(p.provider_call_rows, 0) AS provider_call_rows,
            COALESCE(p.provider_error_rows, 0) AS provider_error_rows,
            COALESCE(p.provider_incomplete_rows, 0) AS provider_incomplete_rows,
            COALESCE(x.provider_result_rows, 0) AS provider_result_rows,
            COALESCE(x.eligible_result_rows, 0) AS eligible_result_rows
        FROM {schema}.adaptive_search_rounds r
        LEFT JOIN branch_counts b USING (run_key, round_index)
        LEFT JOIN provider_counts p USING (run_key, round_index)
        LEFT JOIN result_counts x USING (run_key, round_index)
        """,
        f"""
        CREATE OR REPLACE VIEW {schema}.vw_adaptive_followup_yield AS
        WITH provider_counts AS (
            SELECT
                run_key,
                branch_index,
                COUNT(*) AS provider_call_rows,
                COUNT(*) FILTER (WHERE status = 'error') AS provider_error_rows,
                COUNT(*) FILTER (WHERE status = 'incomplete') AS provider_incomplete_rows
            FROM {schema}.provider_calls
            GROUP BY run_key, branch_index
        )
        SELECT
            v.run_key,
            v.variant_id,
            v.variant_order,
            v.query_text,
            v.selected,
            v.executed,
            v.skip_reason,
            b.branch_id,
            b.branch_index,
            b.branch_role,
            b.branch_why,
            b.assigned_providers,
            b.attempted_providers,
            b.results_count,
            b.latency_ms,
            COALESCE(p.provider_call_rows, 0) AS provider_call_rows,
            COALESCE(p.provider_error_rows, 0) AS provider_error_rows,
            COALESCE(p.provider_incomplete_rows, 0) AS provider_incomplete_rows,
            COALESCE(c.discovered_unique, 0) AS discovered_unique,
            COALESCE(c.exclusive_candidates, 0) AS exclusive_candidates,
            COALESCE(c.fractional_discovery_credit, 0) AS fractional_discovery_credit,
            COALESCE(c.eligible_candidates, 0) AS eligible_candidates,
            COALESCE(c.merged_candidates, 0) AS merged_candidates,
            COALESCE(c.final_candidates, 0) AS final_candidates
        FROM {schema}.query_variants v
        JOIN {schema}.search_branches b
            ON b.run_key = v.run_key
           AND b.branch_id = v.branch_id
        LEFT JOIN provider_counts p
            ON p.run_key = b.run_key
           AND p.branch_index = b.branch_index
        LEFT JOIN {schema}.vw_branch_contribution c
            ON c.run_key = b.run_key
           AND c.branch_id = b.branch_id
        JOIN {schema}.adaptive_search_runs a
            ON a.run_key = v.run_key
        WHERE v.variant_role = 'followup'
        """,
        f"""
        CREATE OR REPLACE VIEW {schema}.vw_adaptive_output_followthrough AS
        SELECT f.*
        FROM {schema}.vw_followup_attribution f
        JOIN {schema}.adaptive_search_runs a USING (run_key)
        WHERE f.tool_name = 'web_search'
        """,
        f"""
        CREATE OR REPLACE VIEW {schema}.vw_adaptive_search_end_to_end AS
        WITH rounds AS (
            SELECT
                run_key,
                COUNT(*) AS round_count,
                MAX(round_index) AS last_round_index,
                SUM(new_url_count) AS summed_new_url_count,
                SUM(domain_count) AS summed_domain_count,
                SUM(provider_failure_count) AS summed_provider_failures
            FROM {schema}.vw_adaptive_search_round
            GROUP BY run_key
        ), followups AS (
            SELECT
                run_key,
                COUNT(*) AS followup_branch_count,
                SUM(discovered_unique) AS followup_discovered_unique,
                SUM(exclusive_candidates) AS followup_exclusive_candidates,
                SUM(merged_candidates) AS followup_merged_candidates,
                SUM(final_candidates) AS followup_final_candidates
            FROM {schema}.vw_adaptive_followup_yield
            GROUP BY run_key
        ), outputs AS (
            SELECT
                run_key,
                COUNT(*) AS output_item_count,
                SUM(fetch_attempts) AS fetch_attempts,
                SUM(successful_fetches) AS successful_fetches
            FROM {schema}.vw_adaptive_output_followthrough
            GROUP BY run_key
        )
        SELECT
            r.*,
            COALESCE(x.round_count, 0) AS round_count,
            x.last_round_index,
            COALESCE(x.summed_new_url_count, 0) AS summed_new_url_count,
            COALESCE(x.summed_domain_count, 0) AS summed_domain_count,
            COALESCE(x.summed_provider_failures, 0) AS summed_provider_failures,
            COALESCE(f.followup_branch_count, 0) AS followup_branch_count,
            COALESCE(f.followup_discovered_unique, 0) AS followup_discovered_unique,
            COALESCE(f.followup_exclusive_candidates, 0) AS followup_exclusive_candidates,
            COALESCE(f.followup_merged_candidates, 0) AS followup_merged_candidates,
            COALESCE(f.followup_final_candidates, 0) AS followup_final_candidates,
            COALESCE(o.output_item_count, 0) AS output_item_count,
            COALESCE(o.fetch_attempts, 0) AS fetch_attempts,
            COALESCE(o.successful_fetches, 0) AS successful_fetches
        FROM {schema}.vw_adaptive_search_run r
        LEFT JOIN rounds x USING (run_key)
        LEFT JOIN followups f USING (run_key)
        LEFT JOIN outputs o USING (run_key)
        """,
    ]


__all__ = ["_build_adaptive_view_sql"]
