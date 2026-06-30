from __future__ import annotations


def build_observability_view_sql(target: str) -> list[str]:
    return [
        f"""
        CREATE OR REPLACE VIEW {target}.vw_web_search_returned_results AS
        SELECT
            tool_call_id,
            run_key,
            recorded_at,
            cache_hit,
            result_rank,
            title,
            link,
            snippet,
            domain,
            providers,
            provider_count,
            score,
            candidate_id,
            canonical_result_id,
            payload_json
        FROM {target}.web_search_response_results
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_provider_health_history AS
        SELECT
            provider,
            transition,
            run_key,
            tool_call_id,
            recorded_at,
            status,
            consecutive_failures,
            cooldown_seconds,
            cooldown_remaining_s,
            total_successes,
            total_failures,
            error_type,
            is_rate_limit,
            circuit_state,
            payload_json
        FROM {target}.provider_health_transitions
        ORDER BY recorded_at DESC
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_provider_lifecycle AS
        WITH call_counts AS (
            SELECT provider, COUNT(*) AS provider_calls, SUM(COALESCE(num_results_returned, 0)) AS results_returned
            FROM {target}.provider_calls
            GROUP BY provider
        ),
        transition_counts AS (
            SELECT
                provider,
                COUNT(*) FILTER (WHERE transition = 'success') AS health_successes,
                COUNT(*) FILTER (WHERE transition = 'failure') AS health_failures,
                COUNT(*) FILTER (WHERE circuit_state = 'open') AS health_open_events
            FROM {target}.provider_health_transitions
            GROUP BY provider
        )
        SELECT
            c.provider,
            COALESCE(cc.provider_calls, 0) AS provider_calls,
            COALESCE(cc.results_returned, 0) AS results_returned,
            COALESCE(tc.health_successes, 0) AS health_successes,
            COALESCE(tc.health_failures, 0) AS health_failures,
            COALESCE(tc.health_open_events, 0) AS health_open_events
        FROM (
            SELECT DISTINCT provider FROM {target}.provider_calls
            UNION
            SELECT DISTINCT provider FROM {target}.provider_health_transitions
        ) c
        LEFT JOIN call_counts cc ON c.provider = cc.provider
        LEFT JOIN transition_counts tc ON c.provider = tc.provider
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_branch_lifecycle AS
        WITH candidate_counts AS (
            SELECT branch_attempt_id, COUNT(*) AS branch_candidate_count
            FROM {target}.branch_candidates
            GROUP BY branch_attempt_id
        )
        SELECT
            b.run_key,
            b.branch_attempt_id,
            b.recorded_at,
            b.branch_index,
            b.branch_type,
            b.branch_query,
            b.branch_weight,
            b.provider_names,
            b.provider_count,
            b.status,
            b.deadline_seconds,
            b.latency_ms,
            b.result_count,
            COALESCE(c.branch_candidate_count, 0) AS branch_candidate_count,
            b.error_type,
            b.error_message,
            b.payload_json
        FROM {target}.branch_attempts b
        LEFT JOIN candidate_counts c USING (branch_attempt_id)
        ORDER BY b.recorded_at DESC
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_pipeline_summary AS
        WITH tool_rows AS (
            SELECT
                run_key,
                tool_call_id,
                MAX(recorded_at) AS tool_call_at,
                MAX(cache_hit) AS cache_hit,
                MAX(num_results_requested) AS num_results_requested,
                MAX(num_results_returned) AS num_results_returned,
                MAX(result_offset) AS result_offset
            FROM {target}.web_search_tool_calls
            GROUP BY run_key, tool_call_id
        ),
        response_rows AS (
            SELECT
                run_key,
                tool_call_id,
                COUNT(*) AS returned_rows
            FROM {target}.web_search_response_results
            GROUP BY run_key, tool_call_id
        ),
        branch_rows AS (
            SELECT run_key, COUNT(*) AS branch_count, SUM(result_count) AS branch_results
            FROM {target}.branch_attempts
            GROUP BY run_key
        ),
        provider_rows AS (
            SELECT run_key, COUNT(*) AS provider_calls
            FROM {target}.provider_calls
            GROUP BY run_key
        ),
        merged_rows AS (
            SELECT run_key, COUNT(*) AS merged_count
            FROM {target}.merged_candidates
            GROUP BY run_key
        ),
        rerank_rows AS (
            SELECT run_key, COUNT(*) AS rerank_rows, MAX(output_count) AS reranked_count
            FROM {target}.rerank_stages
            GROUP BY run_key
        ),
        final_rows AS (
            SELECT run_key, COUNT(*) AS final_count
            FROM {target}.final_results
            GROUP BY run_key
        )
        SELECT
            r.run_key,
            MIN(r.query) AS query,
            MIN(r.normalized_query) AS normalized_query,
            MIN(r.duration_ms) AS duration_ms,
            MIN(r.status) AS status,
            MIN(t.cache_hit) AS cache_hit,
            COALESCE(MAX(b.branch_count), 0) AS branch_count,
            COALESCE(MAX(p.provider_calls), 0) AS provider_calls,
            COALESCE(MAX(m.merged_count), 0) AS merged_count,
            COALESCE(MAX(rr.reranked_count), 0) AS reranked_count,
            COALESCE(MAX(f.final_count), 0) AS final_count,
            COALESCE(MAX(resp.returned_rows), 0) AS returned_count,
            MIN(t.num_results_requested) AS num_results_requested,
            MIN(t.result_offset) AS result_offset,
            MIN(t.tool_call_at) AS tool_call_at,
            MAX(r.recorded_at) AS search_run_recorded_at
        FROM {target}.search_runs r
        LEFT JOIN tool_rows t ON r.run_key = t.run_key
        LEFT JOIN response_rows resp ON r.run_key = resp.run_key AND t.tool_call_id = resp.tool_call_id
        LEFT JOIN branch_rows b ON r.run_key = b.run_key
        LEFT JOIN provider_rows p ON r.run_key = p.run_key
        LEFT JOIN merged_rows m ON r.run_key = m.run_key
        LEFT JOIN rerank_rows rr ON r.run_key = rr.run_key
        LEFT JOIN final_rows f ON r.run_key = f.run_key
        GROUP BY r.run_key
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_stage_timeline AS
        SELECT run_key, recorded_at, 'search_run' AS stage, duration_ms, payload_json
        FROM {target}.search_runs
        UNION ALL
        SELECT run_key, recorded_at, 'branch_attempt' AS stage, latency_ms AS duration_ms, payload_json
        FROM {target}.branch_attempts
        UNION ALL
        SELECT run_key, recorded_at, 'provider_call' AS stage, duration_ms, payload_json
        FROM {target}.provider_calls
        UNION ALL
        SELECT run_key, recorded_at, 'merged_candidate' AS stage, NULL AS duration_ms, payload_json
        FROM {target}.merged_candidates
        UNION ALL
        SELECT run_key, recorded_at, 'rerank_stage' AS stage, duration_ms, payload_json
        FROM {target}.rerank_stages
        UNION ALL
        SELECT run_key, recorded_at, 'final_result' AS stage, NULL AS duration_ms, payload_json
        FROM {target}.final_results
        UNION ALL
        SELECT run_key, recorded_at, 'tool_response' AS stage, NULL AS duration_ms, payload_json
        FROM {target}.web_search_response_results
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_pipeline_freshness AS
        SELECT
            r.run_key,
            r.query,
            r.normalized_query,
            r.recorded_at AS search_run_recorded_at,
            hb.recorded_at AS heartbeat_recorded_at,
            hb.stage AS heartbeat_stage,
            DATE_DIFF('second', hb.recorded_at, CURRENT_TIMESTAMP) AS heartbeat_age_seconds,
            r.duration_ms,
            hb.duration_ms AS heartbeat_duration_ms,
            hb.branch_count,
            hb.provider_count,
            hb.merged_count,
            hb.reranked_count,
            hb.final_count,
            hb.returned_count,
            hb.cache_hit
        FROM {target}.search_runs r
        LEFT JOIN (
            SELECT *
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (PARTITION BY run_key ORDER BY recorded_at DESC) AS rn
                FROM {target}.pipeline_heartbeats
            ) heartbeat_ranked
            WHERE rn = 1
        ) hb ON r.run_key = hb.run_key
        """,
    ]

