"""Quality metrics computation for search runs.

Queries the DuckDB pipeline tables for a given ``run_key``, computes derived
quality metrics, and inserts the result into ``search_quality_scores`` via the
existing ``insert_search_quality_scores`` function.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import duckdb

from .duckdb_store import (
    _db_path,
    ensure_search_quality_tables,
    insert_search_quality_scores,
)


def compute_positional_discount(label: float, position: int) -> float:
    """Return a zero-based logarithmically discounted relevance label."""
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        raise ValueError("position must be a nonnegative zero-based integer")
    value = float(label)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("label must be a nonnegative finite number")
    return value / math.log2(position + 2)


def compute_discounted_cumulative_gain(
    labels: Iterable[float], *, k: int | None = None
) -> float:
    """Compute discounted cumulative gain from labels ordered by position."""
    values = list(labels)
    if k is not None:
        if isinstance(k, bool) or not isinstance(k, int) or k < 0:
            raise ValueError("k must be a nonnegative integer or None")
        values = values[:k]
    return sum(compute_positional_discount(value, position) for position, value in enumerate(values))


def replay_result_labels_aggregate(
    *,
    run_key: str | None = None,
    rubric_version: str | None = None,
    source: str | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Read grouped positional label gains without changing live ranking."""
    path = _db_path(db_path)
    ensure_search_quality_tables(db_path=str(path))
    clauses: list[str] = []
    params: list[str] = []
    for column, value in (
        ("run_key", run_key),
        ("rubric_version", rubric_version),
        ("source", source),
    ):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    connection = duckdb.connect(str(path), read_only=True)
    try:
        rows = connection.execute(
            f"""
            SELECT
                run_key,
                stage,
                source,
                rubric_version,
                COUNT(*) AS label_count,
                SUM(discounted_gain) AS discounted_gain
            FROM result_labels
            {where}
            GROUP BY run_key, stage, source, rubric_version
            ORDER BY run_key, stage, source, rubric_version
            """,
            params,
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "run_key": row[0],
            "stage": row[1],
            "source": row[2],
            "rubric_version": row[3],
            "label_count": int(row[4]),
            "discounted_gain": float(row[5] or 0.0),
        }
        for row in rows
    ]

def compute_search_quality(run_key: str, db_path: str | None = None) -> dict[str, object]:
    """Query DuckDB tables for *run_key* and insert computed quality metrics.

    Metrics are computed from the live pipeline tables: ``search_candidates``,
    ``final_results``, ``rerank_stages``, ``rerank_candidates``,
    ``search_branches``, and ``provider_calls``. The result is written to
    ``search_quality_scores`` using ``insert_search_quality_scores``.

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

    con = duckdb.connect(str(path))
    try:
        row = con.execute(
            """
            SELECT CAST(SUM(CASE WHEN overlap_flag THEN 1 ELSE 0 END) AS DOUBLE)
            / NULLIF(COUNT(*), 0)
            FROM search_candidates WHERE run_key = ?
            """,
            [run_key],
        ).fetchone()
        provider_overlap_rate = row[0] if row else None
        row = con.execute(
            "SELECT COUNT(DISTINCT domain) FROM final_results WHERE run_key = ?", [run_key]
        ).fetchone()
        domain_diversity_count = row[0] if row else None
        row = con.execute(
            "SELECT COUNT(*) FROM final_results WHERE run_key = ?", [run_key]
        ).fetchone()
        total_final_results = row[0] if row else None
        domain_diversity_ratio = (
            domain_diversity_count / total_final_results
            if domain_diversity_count is not None and total_final_results
            else None
        )
        row = con.execute(
            "SELECT CAST(SUM(input_count) AS DOUBLE) / NULLIF(SUM(output_count), 0) FROM rerank_stages WHERE run_key = ?",
            [run_key],
        ).fetchone()
        rerank_compression_ratio = row[0] if row and row[0] is not None else None
        row = con.execute(
            "SELECT AVG(rrf_score) FROM search_candidates WHERE run_key = ?", [run_key]
        ).fetchone()
        avg_rrf_score = row[0] if row else None
        row = con.execute(
            "SELECT MAX(score_after) FROM rerank_candidates WHERE run_key = ?", [run_key]
        ).fetchone()
        top_score = row[0] if row else None
        row = con.execute(
            "SELECT approx_quantile(score_after, 0.95) FROM rerank_candidates WHERE run_key = ?",
            [run_key],
        ).fetchone()
        p95_score = row[0] if row else None
        row = con.execute(
            "SELECT COUNT(*) FROM search_branches WHERE run_key = ?", [run_key]
        ).fetchone()
        branch_count = row[0] if row else None
        row = con.execute(
            "SELECT COUNT(DISTINCT provider) FROM provider_calls WHERE run_key = ?", [run_key]
        ).fetchone()
        provider_count = row[0] if row else None
        row = con.execute(
            "SELECT SUM(num_results_returned) FROM provider_calls WHERE run_key = ?", [run_key]
        ).fetchone()
        total_candidates_input = row[0] if row else None
        row = con.execute(
            "SELECT COUNT(*) FROM search_candidates WHERE run_key = ?", [run_key]
        ).fetchone()
        total_candidates_merged = row[0] if row else None
        row = con.execute(
            "SELECT SUM(output_count) FROM rerank_stages WHERE run_key = ?", [run_key]
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
