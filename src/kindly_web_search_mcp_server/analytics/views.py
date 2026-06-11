"""Human-readable analytics views for search quality inspection.

All views are idempotent (CREATE OR REPLACE VIEW).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import duckdb

from .duckdb_store import _db_path
from ..settings import settings

_LOCK = threading.Lock()


VIEW_DEFINITIONS: dict[str, str] = {
    "v_search_run_story": """
        WITH qu AS (
            SELECT run_key, intent, confidence FROM query_understanding
        ),
        qr_counts AS (
            SELECT run_key, COUNT(*) AS cnt FROM query_rewrites GROUP BY run_key
        ),
        pc_counts AS (
            SELECT run_key, COUNT(*) AS cnt, AVG(duration_ms) AS avg_latency FROM provider_calls GROUP BY run_key
        ),
        prc_counts AS (
            SELECT run_key, COUNT(*) AS cnt FROM provider_candidates GROUP BY run_key
        ),
        mc_counts AS (
            SELECT run_key, COUNT(*) AS cnt FROM merged_candidates GROUP BY run_key
        ),
        fr_counts AS (
            SELECT run_key, COUNT(*) AS cnt FROM final_results GROUP BY run_key
        ),
        rs_latency AS (
            SELECT run_key, AVG(duration_ms) AS avg_latency FROM rerank_stages GROUP BY run_key
        ),
        sqs AS (
            SELECT run_key, provider_overlap_rate, domain_diversity_count FROM search_quality_scores
        )
        SELECT
            r.run_key,
            r.query,
            r.normalized_query,
            r.research_goal,
            r.status,
            r.duration_ms AS total_duration_ms,
            r.candidate_count,
            r.rewrite_enabled,
            r.recorded_at AS run_recorded_at,
            COALESCE(qr.cnt, 0) AS rewrite_variant_count,
            COALESCE(pc.cnt, 0) AS provider_call_count,
            COALESCE(prc.cnt, 0) AS provider_candidate_count,
            COALESCE(mc.cnt, 0) AS merged_candidate_count,
            COALESCE(fr.cnt, 0) AS final_result_count,
            pc.avg_latency AS avg_provider_latency_ms,
            rs.avg_latency AS avg_rerank_latency_ms,
            qu.intent,
            qu.confidence,
            sqs.provider_overlap_rate AS overlap_rate,
            sqs.domain_diversity_count AS domain_diversity
        FROM search_runs r
        LEFT JOIN qu ON r.run_key = qu.run_key
        LEFT JOIN qr_counts qr ON r.run_key = qr.run_key
        LEFT JOIN pc_counts pc ON r.run_key = pc.run_key
        LEFT JOIN prc_counts prc ON r.run_key = prc.run_key
        LEFT JOIN mc_counts mc ON r.run_key = mc.run_key
        LEFT JOIN fr_counts fr ON r.run_key = fr.run_key
        LEFT JOIN rs_latency rs ON r.run_key = rs.run_key
        LEFT JOIN sqs ON r.run_key = sqs.run_key
    """,

    "v_provider_survival_funnel": """
        WITH provider_runs AS (
            SELECT provider, run_key, COUNT(*) AS candidates
            FROM provider_candidates
            GROUP BY provider, run_key
        ),
        merged_links AS (
            SELECT mc.run_key, mc.link
            FROM merged_candidates mc
        ),
        final_links AS (
            SELECT fr.run_key, fr.link
            FROM final_results fr
        ),
        provider_merged AS (
            SELECT
                pr.provider,
                pr.run_key,
                COUNT(DISTINCT ml.link) AS merged_count
            FROM provider_runs pr
            LEFT JOIN merged_links ml
                ON pr.run_key = ml.run_key
            LEFT JOIN provider_candidates pc
                ON pr.run_key = pc.run_key AND pr.provider = pc.provider AND pc.link = ml.link
            WHERE pc.link IS NOT NULL
            GROUP BY pr.provider, pr.run_key
        ),
        provider_final AS (
            SELECT
                pr.provider,
                pr.run_key,
                COUNT(DISTINCT fl.link) AS final_count
            FROM provider_runs pr
            LEFT JOIN final_links fl
                ON pr.run_key = fl.run_key
            LEFT JOIN provider_candidates pc
                ON pr.run_key = pc.run_key AND pr.provider = pc.provider AND pc.link = fl.link
            WHERE pc.link IS NOT NULL
            GROUP BY pr.provider, pr.run_key
        )
        SELECT
            pr.provider,
            COUNT(DISTINCT pr.run_key) AS runs_with_provider,
            SUM(pr.candidates) AS provider_candidates,
            SUM(COALESCE(pm.merged_count, 0)) AS merged_candidates,
            SUM(COALESCE(pf.final_count, 0)) AS final_results,
            ROUND(
                100.0 * SUM(COALESCE(pf.final_count, 0)) / NULLIF(COUNT(DISTINCT pr.run_key), 0),
                2
            ) AS survival_rate_pct
        FROM provider_runs pr
        LEFT JOIN provider_merged pm ON pr.provider = pm.provider AND pr.run_key = pm.run_key
        LEFT JOIN provider_final pf ON pr.provider = pf.provider AND pr.run_key = pf.run_key
        GROUP BY pr.provider
        ORDER BY survival_rate_pct DESC NULLS LAST
    """,

    "v_rewrite_effectiveness": """
        SELECT
            r.run_key,
            r.query,
            r.rewrite_enabled,
            COUNT(DISTINCT qr.variant_index) AS variant_count,
            COUNT(DISTINCT pc.provider) AS providers_used,
            COUNT(DISTINCT prc.link) AS distinct_candidates,
            r.final_result_count
        FROM search_runs r
        LEFT JOIN query_rewrites qr ON r.run_key = qr.run_key
        LEFT JOIN provider_calls pc ON r.run_key = pc.run_key
        LEFT JOIN provider_candidates prc ON r.run_key = prc.run_key
        GROUP BY r.run_key, r.query, r.rewrite_enabled, r.final_result_count
    """,

    "v_rerank_stage_performance": """
        SELECT
            rs.stage,
            rs.provider,
            rs.model,
            COUNT(DISTINCT rs.run_key) AS runs,
            AVG(rs.input_count) AS avg_input_count,
            AVG(rs.output_count) AS avg_output_count,
            AVG(rs.duration_ms) AS avg_duration_ms,
            AVG(rs.max_score) AS avg_max_score,
            AVG(rs.avg_score) AS avg_avg_score,
            SUM(CASE WHEN rs.entity_overlap_enabled THEN 1 ELSE 0 END) AS entity_overlap_runs
        FROM rerank_stages rs
        GROUP BY rs.stage, rs.provider, rs.model
    """,

    "v_daily_quality_summary": """
        SELECT
            CAST(r.recorded_at AS DATE) AS day,
            COUNT(DISTINCT r.run_key) AS query_count,
            AVG(r.duration_ms) AS avg_total_latency_ms,
            AVG(sqs.provider_overlap_rate) AS avg_overlap_rate,
            AVG(sqs.domain_diversity_count) AS avg_domain_diversity,
            AVG(sqs.rerank_compression_ratio) AS avg_compression_ratio,
            AVG(sqs.top_score) AS avg_top_score,
            AVG(je.overall_score) AS avg_judge_score
        FROM search_runs r
        LEFT JOIN search_quality_scores sqs ON r.run_key = sqs.run_key
        LEFT JOIN judge_evaluations je ON r.run_key = je.run_key
        GROUP BY day
        ORDER BY day DESC
    """,
}


def ensure_views(*, db_path: str | None = None) -> None:
    """Create or replace all analytics views."""
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    if not path.exists():
        return
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            for view_name, sql in VIEW_DEFINITIONS.items():
                connection.execute(f"CREATE OR REPLACE VIEW {view_name} AS {sql}")
        finally:
            connection.close()


def refresh_views(*, db_path: str | None = None) -> None:
    """Recreate all views (useful after schema migrations)."""
    ensure_views(db_path=db_path)


# Backward-compatible alias used by analytics/__init__.py
ensure_local_views = ensure_views


def build_analytics_view_sql(schema: str) -> list[str]:
    """Return list of SQL statements to create views in a target schema (e.g. MotherDuck).

    Each statement is a CREATE OR REPLACE TABLE AS SELECT (materialized) since
    MotherDuck views over remote tables can be slow.
    """
    statements = []
    for view_name, sql in VIEW_DEFINITIONS.items():
        # Materialize as table for MotherDuck performance
        statements.append(
            f"CREATE OR REPLACE TABLE {schema}.{view_name} AS {sql}"
        )
    return statements
