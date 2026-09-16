def _build_fetch_observability_view_sql(target: str) -> list[str]:
    """Return SQL for fetch-tool stage, backend quality, follow-through, and freshness views."""
    t = target
    return [
        f"""
        CREATE OR REPLACE VIEW {t}.vw_fetch_stage_funnel AS
        SELECT
            stage,
            COUNT(*) AS attempts,
            SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN outcome = 'partial' THEN 1 ELSE 0 END) AS partials,
            SUM(CASE WHEN outcome = 'skipped' THEN 1 ELSE 0 END) AS skipped,
            AVG(CASE WHEN outcome IN ('success', 'partial') THEN latency_ms END) AS avg_win_latency_ms,
            AVG(quality_score) AS avg_quality
        FROM content_stage_attempts
        GROUP BY stage
        """,
        f"""
        CREATE OR REPLACE VIEW {t}.vw_fetch_backend_quality AS
        SELECT
            f.fetch_backend,
            COUNT(*) AS fetches,
            AVG(i.quality_score) AS avg_quality,
            SUM(CASE WHEN f.status = 'success' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS success_rate,
            SUM(CASE WHEN i.error_retryable THEN 1 ELSE 0 END) AS retryable_errors
        FROM content_fetches f
        LEFT JOIN content_fetch_items i
            ON f.terminal_event_id = i.terminal_event_id
            AND f.tool_call_id = i.tool_call_id
            AND f.item_index = i.item_index
        GROUP BY f.fetch_backend
        """,
        f"""
        CREATE OR REPLACE VIEW {t}.vw_fetch_followthrough AS
        SELECT
            f.tool_call_id,
            f.normalized_url AS fetched_url,
            f.fetch_backend,
            f.source_type,
            f.status AS fetch_status,
            fr.link AS result_link,
            fr.domain AS result_domain,
            fr.rank AS result_rank,
            fr.run_key AS result_run_key,
            (fr.link IS NOT NULL) AS was_search_result
        FROM content_fetches f
        LEFT JOIN final_results fr
            ON lower(fr.link) = lower(f.normalized_url)
        """,
        f"""
        CREATE OR REPLACE VIEW {t}.vw_analytics_table_freshness AS
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY table_name ORDER BY checked_at DESC) AS rn
            FROM analytics_table_freshness
        )
        SELECT table_name, checked_at, max_recorded_at, row_count
        FROM ranked
        WHERE rn = 1
        ORDER BY table_name
        """,
    ]
