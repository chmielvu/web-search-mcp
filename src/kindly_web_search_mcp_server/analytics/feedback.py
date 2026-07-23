"""Quality feedback loop — drift detection, SLO monitoring, regression flagging.

Integrates patterns from the flocksketch analytics pipeline:
- 10-min SMA drift detection using RANGE BETWEEN INTERVAL
- Per-provider SLO compliance (Google SRE workbook pattern)
- NDCG@10 with per-run IDCG (Databricks 4-point scale)
- Intent × provider quality matrix via PIVOT
- QUALIFY top-N regression candidates
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import duckdb

from .duckdb_store import _db_path

logger = logging.getLogger(__name__)

# Databricks 4-point graded relevance: score ranges → grade
# Gain = 2^grade - 1 for DCG calculation
_GRADE_BINS = [
    (0.8, 3, 7),  # ≥ 0.8: Highly relevant, gain 7
    (0.5, 2, 3),  # 0.5–0.8: Relevant, gain 3
    (0.3, 1, 1),  # 0.3–0.5: Marginal, gain 1
    (0.0, 0, 0),  # < 0.3: Not relevant, gain 0
]


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(_db_path()), read_only=True)


def detect_drift(window_minutes: int = 10, threshold: float = 0.1) -> list[dict[str, Any]]:
    """Find runs where composite quality dropped significantly below the SMA."""
    conn = _connect()
    try:
        result = conn.execute(f"""
            WITH smoothed AS (
                SELECT run_key, recorded_at, composite_quality,
                       AVG(composite_quality) OVER (
                           ORDER BY recorded_at
                           RANGE BETWEEN INTERVAL '{window_minutes} MINUTE' PRECEDING
                                     AND CURRENT ROW
                       ) AS sma
                FROM search_quality_scores
            )
            SELECT run_key, recorded_at, composite_quality, sma,
                   ROUND((sma - composite_quality)::DOUBLE, 4) AS delta_below_sma
            FROM smoothed
            WHERE (sma - composite_quality) > {threshold}
            ORDER BY delta_below_sma DESC
        """).fetchall()
        return [
            {
                "run_key": row[0],
                "recorded_at": row[1].isoformat(),
                "composite_quality": row[2],
                "sma": row[3],
                "delta_below_sma": row[4],
            }
            for row in result
        ]
    finally:
        conn.close()


def flag_regressions(top_n: int = 5) -> list[dict[str, Any]]:
    """Top-N runs with the largest quality drop below SMA."""
    conn = _connect()
    try:
        result = conn.execute(f"""
            WITH smoothed AS (
                SELECT run_key, recorded_at, composite_quality,
                       AVG(composite_quality) OVER (
                           ORDER BY recorded_at
                           RANGE BETWEEN INTERVAL '10 MINUTE' PRECEDING
                                     AND CURRENT ROW
                       ) AS sma
                FROM search_quality_scores
            )
            SELECT run_key, recorded_at, composite_quality, sma,
                   ROUND((sma - composite_quality)::DOUBLE, 4) AS delta_below_sma
            FROM smoothed
            QUALIFY ROW_NUMBER() OVER (ORDER BY sma - composite_quality DESC) <= {top_n}
            ORDER BY delta_below_sma DESC
        """).fetchall()
        return [
            {
                "run_key": row[0],
                "recorded_at": row[1].isoformat(),
                "composite_quality": row[2],
                "sma": row[3],
                "delta_below_sma": row[4],
            }
            for row in result
        ]
    finally:
        conn.close()


def compute_slo_compliance(
    latency_slo_ms: dict[str, int] | None = None,
) -> dict[str, dict[str, float]]:
    """Provider-level SLO compliance using good/total ratios.

    Args:
        latency_slo_ms: {"p50": 5000, "p95": 60000} — SLO thresholds in ms.
    """
    if latency_slo_ms is None:
        latency_slo_ms = {"p50": 5000, "p95": 60000}

    conn = _connect()
    try:
        result: dict[str, dict[str, float]] = {}
        for provider_row in conn.execute(
            "SELECT DISTINCT provider FROM provider_calls WHERE recorded_at > now() - INTERVAL '7 days'"
        ).fetchall():
            provider = provider_row[0]
            metrics: dict[str, float] = {"coverage_pct": 0.0}
            # Coverage: % of unique runs this provider was called for
            total_runs = conn.execute(
                "SELECT COUNT(DISTINCT run_key) FROM search_runs WHERE recorded_at > now() - INTERVAL '7 days'"
            ).fetchone()[0]
            provider_runs = conn.execute(
                "SELECT COUNT(DISTINCT run_key) FROM provider_calls "
                "WHERE provider = ? AND recorded_at > now() - INTERVAL '7 days'",
                [provider],
            ).fetchone()[0]
            if total_runs:
                metrics["coverage_pct"] = round(100.0 * provider_runs / total_runs, 1)

            # Latency SLO compliance
            for label, threshold in latency_slo_ms.items():
                row = conn.execute(
                    "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE latency_ms <= ?) / NULLIF(COUNT(*), 0), 1) "
                    "FROM provider_calls WHERE provider = ? AND recorded_at > now() - INTERVAL '7 days'",
                    [threshold, provider],
                ).fetchone()
                metrics[f"latency_{label}_compliance_pct"] = row[0] if row[0] is not None else 0.0

            # Error rate
            error_row = conn.execute(
                "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status != 'success') / NULLIF(COUNT(*), 0), 1) "
                "FROM provider_calls WHERE provider = ? AND recorded_at > now() - INTERVAL '7 days'",
                [provider],
            ).fetchone()
            metrics["error_rate_pct"] = error_row[0] if error_row[0] is not None else 0.0

            result[provider] = metrics
        return result
    finally:
        conn.close()


def compute_ndcg_at_10() -> list[dict[str, Any]]:
    """Per-run NDCG@10 using Databricks 4-point graded relevance."""
    conn = _connect()
    try:
        # Grade and gain via CASE WHEN inline
        grade_expr = "".join(
            f"WHEN je.relevance_score >= {lo} THEN {grade}" for lo, grade, _ in _GRADE_BINS
        )
        gain_expr = "".join(
            f"WHEN je.relevance_score >= {lo} THEN {gain}" for lo, _, gain in _GRADE_BINS
        )

        result = conn.execute(f"""
            WITH graded AS (
                SELECT je.run_key, je.recorded_at,
                       CASE {grade_expr} ELSE 0 END AS grade,
                       CASE {gain_expr} ELSE 0 END AS gain,
                       ROW_NUMBER() OVER (PARTITION BY je.run_key ORDER BY je.relevance_score DESC) AS rank
                FROM judge_evaluations je
            ),
            dcg AS (
                SELECT run_key, recorded_at,
                       SUM(gain / LOG2(rank + 1)) AS dcg,
                       COUNT(*) AS n
                FROM graded WHERE rank <= 10 GROUP BY run_key, recorded_at
            ),
            idcg AS (
                SELECT run_key,
                       SUM(gain / LOG2(ideal_rank + 1)) AS idcg
                FROM (
                    SELECT run_key, gain,
                           ROW_NUMBER() OVER (
                               PARTITION BY run_key ORDER BY gain DESC
                           ) AS ideal_rank
                    FROM graded
                ) ideal
                JOIN dcg USING (run_key)
                WHERE ideal_rank <= dcg.n
                GROUP BY run_key
            )
            SELECT dcg.run_key, dcg.recorded_at,
                   ROUND(dcg.dcg::DOUBLE, 4) AS dcg,
                   ROUND(idcg.idcg::DOUBLE, 4) AS idcg,
                   ROUND((dcg.dcg / NULLIF(idcg.idcg, 0))::DOUBLE, 4) AS ndcg
            FROM dcg JOIN idcg ON dcg.run_key = idcg.run_key
            ORDER BY dcg.recorded_at DESC
        """).fetchall()
        return [
            {
                "run_key": r[0],
                "recorded_at": r[1].isoformat(),
                "dcg": r[2],
                "idcg": r[3],
                "ndcg": r[4],
            }
            for r in result
        ]
    finally:
        conn.close()


def generate_report() -> str:
    """Generate a markdown quality report."""
    now = datetime.now(timezone.utc).isoformat()
    drift = detect_drift()
    regressions = flag_regressions()
    slo = compute_slo_compliance()

    lines = [f"# Search Quality Report — {now}\n"]
    lines.append(f"## Drift Detection\nRuns with quality >0.1 below SMA: **{len(drift)}**")
    if drift:
        lines.append("\n| Run | Quality | SMA | Delta |")
        lines.append("|-----|---------|-----|-------|")
        for d in drift[:10]:
            lines.append(
                f"| {d['run_key'][:12]} | {d['composite_quality']:.3f} | {d['sma']:.3f} | {d['delta_below_sma']:.3f} |"
            )

    lines.append("\n## Top Regressions\n")
    if regressions:
        lines.append("| Rank | Run | Quality | SMA | Delta |")
        lines.append("|------|-----|---------|-----|-------|")
        for i, r in enumerate(regressions, 1):
            lines.append(
                f"| {i} | {r['run_key'][:12]} | {r['composite_quality']:.3f} | {r['sma']:.3f} | {r['delta_below_sma']:.3f} |"
            )

    lines.append("\n## Provider SLO Compliance\n")
    lines.append("| Provider | Coverage % | p50 Latency % | p95 Latency % | Error Rate % |")
    lines.append("|----------|-----------|---------------|---------------|--------------|")
    for provider, metrics in sorted(slo.items()):
        lines.append(
            f"| {provider} | {metrics['coverage_pct']} | "
            f"{metrics.get('latency_p50_compliance_pct', '-')} | "
            f"{metrics.get('latency_p95_compliance_pct', '-')} | "
            f"{metrics['error_rate_pct']} |"
        )

    return "\n".join(lines)
