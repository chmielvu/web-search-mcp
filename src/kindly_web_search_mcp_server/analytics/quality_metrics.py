"""Quality metrics computation for search runs.

Queries the DuckDB pipeline tables for a given ``run_key``, computes derived
quality metrics, and inserts the result into ``search_quality_scores`` via the
existing ``insert_search_quality_scores`` function.
"""

from __future__ import annotations

import duckdb

from .duckdb_store import (
    _db_path,
    ensure_search_quality_tables,
    insert_search_quality_scores,
)
from .writers.connection import _LOCK


def compute_search_quality(run_key: str, db_path: str | None = None) -> dict[str, object]:
    """Query DuckDB tables for *run_key* and insert computed quality metrics.

    Metrics are computed from the pipeline tables (merged_candidates,
    final_results, rerank_stages, rerank_candidates, query_rewrites,
    provider_calls) and written to ``search_quality_scores`` using the
    existing ``insert_search_quality_scores`` function.

    Parameters
    ----------
    run_key:
        The search-run identifier to compute metrics for.
    db_path:
        Optional explicit path to the DuckDB database.  Defaults to the path
        resolved by ``settings.analytics_duckdb_path``.

    Returns
    -------
    dict[str, object]
        A mapping of metric name → computed value (``None`` for missing data).
    """
    path = _db_path(db_path)
    ensure_search_quality_tables(db_path=str(path))

    with _LOCK:
        con = duckdb.connect(str(path))
        try:
            # ── provider_overlap_rate ──────────────────────────────────────────
            # fraction of merged_candidates where overlap_flag is true
            row = con.execute(
                """
                SELECT
                    CAST(SUM(CASE WHEN overlap_flag THEN 1 ELSE 0 END) AS DOUBLE)
                    / NULLIF(COUNT(*), 0)
                FROM merged_candidates
                WHERE run_key = ?
                """,
                [run_key],
            ).fetchone()
            provider_overlap_rate = row[0] if row else None

            # ── domain_diversity_count & ratio from final_results ──────────────
            row = con.execute(
                "SELECT COUNT(DISTINCT domain) FROM final_results WHERE run_key = ?",
                [run_key],
            ).fetchone()
            domain_diversity_count = row[0] if row else None

            row = con.execute(
                "SELECT COUNT(*) FROM final_results WHERE run_key = ?",
                [run_key],
            ).fetchone()
            total_final_results = row[0] if row else None

            domain_diversity_ratio = None
            if (
                domain_diversity_count is not None
                and total_final_results is not None
                and total_final_results > 0
            ):
                domain_diversity_ratio = domain_diversity_count / total_final_results

            # ── rerank_compression_ratio ───────────────────────────────────────
            row = con.execute(
                """
                SELECT
                    CAST(SUM(input_count) AS DOUBLE)
                    / NULLIF(SUM(output_count), 0)
                FROM rerank_stages
                WHERE run_key = ?
                """,
                [run_key],
            ).fetchone()
            rerank_compression_ratio = row[0] if row and row[0] is not None else None

            # ── avg_rrf_score ──────────────────────────────────────────────────
            row = con.execute(
                "SELECT AVG(rrf_score) FROM merged_candidates WHERE run_key = ?",
                [run_key],
            ).fetchone()
            avg_rrf_score = row[0] if row else None

            # ── top_score from rerank_candidates ───────────────────────────────
            row = con.execute(
                "SELECT MAX(score_after) FROM rerank_candidates WHERE run_key = ?",
                [run_key],
            ).fetchone()
            top_score = row[0] if row else None

            # ── p95_score (approximate quantile from rerank_candidates) ────────
            row = con.execute(
                """
                SELECT approx_quantile(score_after, 0.95)
                FROM rerank_candidates
                WHERE run_key = ?
                """,
                [run_key],
            ).fetchone()
            p95_score = row[0] if row else None

            # ── rewrite_variant_count (and alias branch_count) ─────────────────
            row = con.execute(
                "SELECT COUNT(*) FROM query_rewrites WHERE run_key = ?",
                [run_key],
            ).fetchone()
            rewrite_variant_count = row[0] if row else None
            branch_count = rewrite_variant_count  # alias per plan

            # ── provider_count ─────────────────────────────────────────────────
            row = con.execute(
                "SELECT COUNT(DISTINCT provider) FROM provider_calls WHERE run_key = ?",
                [run_key],
            ).fetchone()
            provider_count = row[0] if row else None

            # ── total_candidates_input ─────────────────────────────────────────
            row = con.execute(
                "SELECT SUM(num_results_returned) FROM provider_calls WHERE run_key = ?",
                [run_key],
            ).fetchone()
            total_candidates_input = row[0] if row else None

            # ── total_candidates_merged ────────────────────────────────────────
            row = con.execute(
                "SELECT COUNT(*) FROM merged_candidates WHERE run_key = ?",
                [run_key],
            ).fetchone()
            total_candidates_merged = row[0] if row else None

            # ── total_candidates_reranked ──────────────────────────────────────
            row = con.execute(
                "SELECT SUM(output_count) FROM rerank_stages WHERE run_key = ?",
                [run_key],
            ).fetchone()
            total_candidates_reranked = row[0] if row else None

        finally:
            con.close()

    # ── Build return dict with native Python types ──────────────────────
    metrics: dict[str, object] = {
        "provider_overlap_rate": _to_float(provider_overlap_rate),
        "domain_diversity_count": _to_int(domain_diversity_count),
        "domain_diversity_ratio": _to_float(domain_diversity_ratio),
        "rerank_compression_ratio": _to_float(rerank_compression_ratio),
        "avg_rrf_score": _to_float(avg_rrf_score),
        "top_score": _to_float(top_score),
        "p95_score": _to_float(p95_score),
        "rewrite_variant_count": _to_int(rewrite_variant_count),
        "provider_count": _to_int(provider_count),
        "branch_count": _to_int(branch_count),
        "total_candidates_input": _to_int(total_candidates_input),
        "total_candidates_merged": _to_int(total_candidates_merged),
        "total_candidates_reranked": _to_int(total_candidates_reranked),
        "total_final_results": _to_int(total_final_results),
    }

    # ── Persist via the existing insert function ────────────────────────
    insert_search_quality_scores(
        run_key=run_key,
        db_path=db_path,
        payload_json=None,
        **metrics,
    )

    return metrics


# ── internal helpers ───────────────────────────────────────────────────────


def _to_float(val: object) -> float | None:
    """Safely convert *val* to ``float`` (or ``None``)."""
    return None if val is None else float(val)  # type: ignore[arg-type]


def _to_int(val: object) -> int | None:
    """Safely convert *val* to ``int`` (or ``None``)."""
    return None if val is None else int(val)  # type: ignore[arg-type]
