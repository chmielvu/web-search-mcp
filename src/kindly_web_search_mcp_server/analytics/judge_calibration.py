"""Calibration metrics for the LLM judge evaluation workflow.

Compares the judge's actual scores (stored in DuckDB) against known
expected scores for a set of queries, computing correlation, MAE,
and bias to quantify how well-calibrated the judge is.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import duckdb

from .duckdb_store import _db_path

logger = logging.getLogger(__name__)

# Score keys used in both judge_evaluations and expected_scores
_SCORE_DIMS = [
    "relevance_score",
    "accuracy_score",
    "completeness_score",
    "source_quality_score",
    "overall_score",
]


def _fetch_judge_scores(
    run_keys: list[str],
    db_path: str | None = None,
) -> dict[str, dict[str, float | None]]:
    """Query judge_evaluations for the given run_keys and return scores keyed by run_key.

    Returns
    -------
    dict[str, dict[str, float | None]]
        {run_key: {relevance_score: ..., overall_score: ..., etc.}}
    """
    path = _db_path(db_path)
    if not path.exists():
        logger.warning("DuckDB path does not exist: %s", path)
        return {}

    con = duckdb.connect(str(path))
    try:
        rows = con.execute(
            f"""
            SELECT run_key,
                   relevance_score,
                   accuracy_score,
                   completeness_score,
                   source_quality_score,
                   overall_score
            FROM judge_evaluations
            WHERE run_key IN ({",".join("?" for _ in run_keys)})
            """,
            run_keys,
        ).fetchall()
    finally:
        con.close()

    result: dict[str, dict[str, float | None]] = {}
    for row in rows:
        run_key = row[0]
        result[run_key] = {
            _SCORE_DIMS[i]: (float(row[i + 1]) if row[i + 1] is not None else None)
            for i in range(len(_SCORE_DIMS))
        }
    return result


def _pearson_correlation(
    actual: list[float],
    expected: list[float],
) -> float:
    """Compute Pearson correlation coefficient between two lists."""
    n = len(actual)
    if n < 2:
        return 0.0

    sum_act = sum(actual)
    sum_exp = sum(expected)
    sum_act_sq = sum(a * a for a in actual)
    sum_exp_sq = sum(e * e for e in expected)
    sum_prod = sum(a * e for a, e in zip(actual, expected, strict=False))

    numerator = n * sum_prod - sum_act * sum_exp
    denom_a = n * sum_act_sq - sum_act * sum_act
    denom_b = n * sum_exp_sq - sum_exp * sum_exp

    if denom_a <= 0 or denom_b <= 0:
        return 0.0

    denominator = math.sqrt(denom_a) * math.sqrt(denom_b)
    if denominator == 0:
        return 0.0

    return numerator / denominator


def calibrate_judge(
    known_queries: list[dict[str, Any]],
    db_path: str | None = None,
) -> dict[str, Any]:
    """Run judge on known queries and compute calibration metrics.

    Each element of *known_queries* must have at least:

    - ``run_key`` (str) — the run_key that was used when the judge evaluation
      was inserted.
    - ``expected_scores`` (dict) — the ground-truth scores for this query.
      Expected keys: ``relevance_score``, ``accuracy_score``,
      ``completeness_score``, ``source_quality_score``, ``overall_score``.
      At minimum ``overall_score`` must be present.

    Parameters
    ----------
    known_queries : list[dict]
        Known-query records with run_key and expected_scores.
    db_path : str or None
        Path to the DuckDB analytics file.  Uses the settings default when
        *None*.

    Returns
    -------
    dict
        ``{"correlation": float, "mean_absolute_error": float,
        "bias": float, "n": int}``
    """
    run_keys = [q["run_key"] for q in known_queries]
    actual_scores = _fetch_judge_scores(run_keys, db_path=db_path)

    # Build paired lists of expected vs actual overall_score
    expected_overall: list[float] = []
    actual_overall: list[float] = []

    per_dim_mae: dict[str, float] = {}
    per_dim_bias: dict[str, float] = {}

    for key in _SCORE_DIMS:
        dim_abs_errors: list[float] = []
        dim_bias_errors: list[float] = []

        for q in known_queries:
            run_key = q["run_key"]
            expected = q.get("expected_scores", {}).get(key)
            if expected is None:
                continue

            actual_row = actual_scores.get(run_key, {})
            actual = actual_row.get(key)
            if actual is None:
                continue

            diff = actual - expected
            dim_abs_errors.append(abs(diff))
            dim_bias_errors.append(diff)

            if key == "overall_score":
                expected_overall.append(expected)
                actual_overall.append(actual)

        if dim_abs_errors:
            per_dim_mae[key] = sum(dim_abs_errors) / len(dim_abs_errors)
            per_dim_bias[key] = sum(dim_bias_errors) / len(dim_bias_errors)

    n = len(expected_overall)

    if n < 2:
        return {
            "correlation": 0.0,
            "mean_absolute_error": per_dim_mae.get("overall_score", 0.0),
            "bias": per_dim_bias.get("overall_score", 0.0),
            "n": n,
            "per_dimension": {
                dim: {
                    "mae": per_dim_mae.get(dim, 0.0),
                    "bias": per_dim_bias.get(dim, 0.0),
                }
                for dim in _SCORE_DIMS
            },
        }

    correlation = _pearson_correlation(actual_overall, expected_overall)
    mae = per_dim_mae.get("overall_score", 0.0)
    bias = per_dim_bias.get("overall_score", 0.0)

    return {
        "correlation": round(correlation, 6),
        "mean_absolute_error": round(mae, 6),
        "bias": round(bias, 6),
        "n": n,
        "per_dimension": {
            dim: {
                "mae": round(per_dim_mae.get(dim, 0.0), 6),
                "bias": round(per_dim_bias.get(dim, 0.0), 6),
            }
            for dim in _SCORE_DIMS
        },
    }


__all__ = ["calibrate_judge"]
