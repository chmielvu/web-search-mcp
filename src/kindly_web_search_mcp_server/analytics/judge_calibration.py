"""Calibration metrics for the LLM judge evaluation workflow.

Compares the judge's actual scores (stored in DuckDB) against known
expected scores for a set of queries, computing correlation, MAE,
and bias to quantify how well-calibrated the judge is.
"""

from __future__ import annotations

import logging
import math
import sys
from typing import Any, Sequence

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


# ============================================================================
# A/B κ Harness -- 6-facet calibration against judge_calibration_set
# ============================================================================
#
# The six facet-decomposed judgments in `analytics/judges.py` use the 120B
# model by default. This harness ADDS A/B rows by re-running the same six
# facets with the 3B model (judge_fast), then computes Cohen's kappa per
# facet, comparing each model's verdict against the hand-adjudicated
# `judge_calibration_set.human_verdict` for the supplied golden run_keys.
#
# Use this for the periodic "DoorDash calibrate" loop (see plan). It is
# NEVER imported by the search path.
#
# Invocation:
#   python -m kindly_web_search_mcp_server.analytics.judge_calibration \\
#       --golden rk1 rk2 rk3 ...


# The six facet kinds (must match judges.JUDGE_FACET_KINDS).
_FACETS: tuple[str, ...] = (
    "run_overview",
    "intent_coherence",
    "rewrite_coverage",
    "rerank_improvement",
    "result_quality",
    "failure_cause",
)

# The two models in the A/B. The 120B (`judge_quality`) is the
# production default; `judge_fast` (3B) is the calibration side.
_PRODUCTION_MODEL = "judge_quality"
_CALIBRATION_MODEL = "judge_fast"


def compute_kappa(
    human: Sequence[str],
    judge: Sequence[str],
    *,
    ordinal: bool = False,
) -> float:
    """Cohen's kappa (binary default) or linear-weighted kappa (ordinal).

    Pure Python implementation -- no scipy dependency (the repo is not
    scipy-dependent, and adding scipy just for a one-shot A/B metric would
    be unjustified weight on this single-user MCP server).

    Binary: w[i,j] = 1 if i==j else 0.
    Ordinal (linear weights): w[i,j] = 1 - |i-j|/(k-1).
    Categories are inferred from the union of inputs and assigned stable
    integer indices via sorted order.

    Returns 0.0 on empty input, 1.0 on perfect agreement (including when
    the chance-expected agreement is already 1.0, which can't happen for
    binary/ordinal with valid input but is the defensive guard).
    """
    n = len(human)
    if n == 0 or len(judge) != n:
        return 0.0
    cats = sorted(set(human) | set(judge))
    cat_to_i = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    if k < 2:
        return 1.0
    counts = [[0] * k for _ in range(k)]
    for h, j in zip(human, judge, strict=True):
        counts[cat_to_i[h]][cat_to_i[j]] += 1
    if ordinal:
        weights = [[1 - abs(i - j) / (k - 1) for j in range(k)] for i in range(k)]
    else:
        weights = [[1.0 if i == j else 0.0 for j in range(k)] for i in range(k)]
    obs = sum(weights[i][j] * counts[i][j] for i in range(k) for j in range(k)) / n
    row_tot = [sum(counts[i][j] for j in range(k)) for i in range(k)]
    col_tot = [sum(counts[i][j] for i in range(k)) for j in range(k)]
    exp = sum(weights[i][j] * row_tot[i] * col_tot[j] for i in range(k) for j in range(k)) / (n * n)
    if exp >= 1.0:
        return 1.0
    return (obs - exp) / (1 - exp)


def _read_verdicts(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_key: str,
    model_name: str,
    rubric_version: str,
) -> dict[str, list[str]]:
    """Read (facet -> [judge_verdict, ...]) from llm_judgments for one model run.

    The `verdict` column carries the compact human-readable form (e.g.
    "intent_match=true; informativeness=3" for result_quality). For a
    fair κ comparison, we project this back to the canonical facet
    verdict category -- the same stringification the calibration set
    uses for human verdicts.
    """
    rows = connection.execute(
        """
        SELECT facet, verdict
        FROM llm_judgments
        WHERE run_key = ?
          AND model_name = ?
          AND rubric_version = ?
          AND status = 'success'
        """,
        [run_key, model_name, rubric_version],
    ).fetchall()
    out: dict[str, list[str]] = {f: [] for f in _FACETS}
    for facet, verdict in rows:
        if facet in out and verdict:
            out[facet].append(verdict)
    return out


def _join_to_human(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_key: str,
    facet: str,
    model_name: str,
    rubric_version: str,
    model_verdicts: list[str],
) -> tuple[list[str], list[str]]:
    """Build paired (human, model) verdict lists for one facet over all golden run_keys.

    For run_keys with no human verdict in `judge_calibration_set`, the
    pair is excluded -- we only score against adjudicated truth.
    """
    human_rows = connection.execute(
        """
        SELECT human_verdict
        FROM judge_calibration_set
        WHERE run_key = ?
          AND facet = ?
          AND model_name = ?
          AND rubric_version = ?
        """,
        [run_key, facet, model_name, rubric_version],
    ).fetchall()
    h = [r[0] for r in human_rows if r[0]]
    # Match index-by-index: a run_key with N model verdicts and M human
    # verdicts contributes min(N, M) pairs. (Test data uses 1 verdict
    # per facet per model per run_key.)
    n_pairs = min(len(h), len(model_verdicts))
    if not n_pairs:
        return [], []
    return h[:n_pairs], model_verdicts[:n_pairs]


def run_calibration(
    golden_run_keys: list[str],
    *,
    rubric_version: str = "v1",
    db_path: str | None = None,
) -> dict[str, dict[str, float]]:
    """A/B κ per facet per model against judge_calibration_set.

    Steps:
      1. Open a connection and run the six facets with the production
         120B model via `judges.judge_search_run` (writes rows tagged
         with `rubric_version='v1'` and `model_name='judge_quality'`).
      2. Rebind `judges._JUDGE_MODEL = 'judge_fast'` and re-run the same
         six facets with the 3B model (writes a second pass of rows
         with `model_name='judge_fast'`, same `rubric_version`).
      3. Restore the original model setting.
      4. Read back verdicts for both models, join to
         `judge_calibration_set.human_verdict`, compute kappa per facet
         per model, and upsert into `judge_rubrics` (the canonical κ
         catalog).

    Returns: `{facet: {model_name: kappa}}`. Facets with no adjudicated
    pairs are omitted from the outer dict.

    Cost: 2 × |golden_run_keys| × |facets_fired_for_each| calls to
    Mistral (the 120B pass dominates). Use sparingly -- this is the
    periodic DoorDash "calibrate" loop, not a per-search evaluation.
    """
    # Lazy imports to avoid module-load-time circulars between
    # judges.py and judge_calibration.py.
    from . import judges
    from .writers.schema import ensure_store_schema
    from .writers.connection import _db_path as _ws_db_path

    ensure_store_schema(db_path=db_path)
    actual_db = str(_ws_db_path(db_path))

    original_model = judges._JUDGE_MODEL
    try:
        judges._JUDGE_MODEL = _PRODUCTION_MODEL
        for rk in golden_run_keys:
            judges.judge_search_run(rk, db_path=actual_db)

        judges._JUDGE_MODEL = _CALIBRATION_MODEL
        for rk in golden_run_keys:
            judges.judge_search_run(rk, db_path=actual_db)
    finally:
        judges._JUDGE_MODEL = original_model

    # Read back verdicts + join to human ground truth + upsert kappas.
    out: dict[str, dict[str, float]] = {}
    connection = duckdb.connect(actual_db, read_only=False)
    try:
        # Group facets that were actually adjudicated so we don't waste
        # empty facet rows in judge_rubrics.
        for facet in _FACETS:
            for model_name in (_PRODUCTION_MODEL, _CALIBRATION_MODEL):
                human_pairs: list[str] = []
                judge_pairs: list[str] = []
                for rk in golden_run_keys:
                    model_verdicts_per_facet = _read_verdicts(
                        connection,
                        run_key=rk,
                        model_name=model_name,
                        rubric_version=rubric_version,
                    ).get(facet, [])
                    h, j = _join_to_human(
                        connection,
                        run_key=rk,
                        facet=facet,
                        model_name=model_name,
                        rubric_version=rubric_version,
                        model_verdicts=model_verdicts_per_facet,
                    )
                    human_pairs.extend(h)
                    judge_pairs.extend(j)
                if not human_pairs:
                    continue
                is_ordinal = facet in (
                    "run_overview",
                    "intent_coherence",
                    "rewrite_coverage",
                    "rerank_improvement",
                    "failure_cause",
                )
                kappa = compute_kappa(human_pairs, judge_pairs, ordinal=is_ordinal)
                out.setdefault(facet, {})[model_name] = kappa
                # Upsert into judge_rubrics.
                connection.execute(
                    """
                    INSERT INTO judge_rubrics (
                        rubric_version, facet, model_name, prompt_name,
                        kappa_score, is_active, created_at
                    ) VALUES (?, ?, ?, ?, ?, true, now())
                    ON CONFLICT (rubric_version, facet, model_name) DO UPDATE
                      SET kappa_score = EXCLUDED.kappa_score,
                          created_at = now()
                    """,
                    [
                        rubric_version,
                        facet,
                        model_name,
                        f"judge_{facet}",
                        kappa,
                    ],
                )
    finally:
        connection.close()
    return out


def _format_table(results: dict[str, dict[str, float]]) -> str:
    """Render the κ result dict as a fixed-width table for CLI output."""
    if not results:
        return "(no adjudicated facets found -- seed judge_calibration_set first)"
    facets = sorted(results.keys())
    models = sorted({m for v in results.values() for m in v})
    header = f"{'facet':24s}  " + "  ".join(f"{m:18s}" for m in models)
    sep = "-" * len(header)
    rows = [header, sep]
    for f in facets:
        line = f"{f:24s}  " + "  ".join(
            f"{results[f].get(m, float('nan')):>+.4f}            "[:18] for m in models
        )
        rows.append(line)
    return "\n".join(rows)


def _main(argv: list[str]) -> int:
    """CLI entrypoint: `python -m ...judge_calibration --golden rk1 rk2 ... [--rubric-version v1]`."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="judge_calibration",
        description="A/B kappa per facet per model against judge_calibration_set.",
    )
    parser.add_argument(
        "--golden",
        nargs="+",
        required=True,
        help="One or more run_keys that have been hand-adjudicated in judge_calibration_set.",
    )
    parser.add_argument(
        "--rubric-version",
        default="v1",
        help="rubric_version to compute/upsert (default: v1).",
    )
    args = parser.parse_args(argv)
    results = run_calibration(args.golden, rubric_version=args.rubric_version)
    print(_format_table(results))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
__all__ = ["calibrate_judge", "compute_kappa", "run_calibration"]
