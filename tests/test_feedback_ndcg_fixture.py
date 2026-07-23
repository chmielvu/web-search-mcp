"""Hand-computed NDCG@10 fixture for compute_ndcg_at_10.

Drives the same DuckDB SQL with a known relevance row set so the Databricks
4-grade mapping, the DCG formula `SUM(gain / LOG2(rank + 1))`, and the IDCG
formula `SUM(gain / LOG2(ideal_rank + 1))` over the same row set are
asserted against pre-computed expected values.
"""

from __future__ import annotations

import math

import duckdb
import pytest

from kindly_web_search_mcp_server.analytics import feedback
from kindly_web_search_mcp_server.analytics.feedback import _GRADE_BINS


def _grade_for(score: float) -> int:
    for lo, grade, _ in _GRADE_BINS:
        if score >= lo:
            return grade
    return 0


def _gain_for(score: float) -> int:
    for lo, _, gain in _GRADE_BINS:
        if score >= lo:
            return gain
    return 0


def _expected_dcg_idcg(relevance: list[float]) -> tuple[float, float]:
    gains = [_gain_for(s) for s in relevance]
    # Top-10 ranking by score desc; DCG uses the actual rank, IDCG uses ideal rank.
    ranked = sorted(enumerate(relevance), key=lambda kv: kv[1], reverse=True)[:10]
    dcg = sum(gains[idx] / math.log2(rank + 1 + 1) for rank, (idx, _) in enumerate(ranked))
    ideal_gains = sorted([gains[i] for i, _ in ranked], reverse=True)
    idcg = sum(g / math.log2(rank + 1 + 1) for rank, g in enumerate(ideal_gains))
    return dcg, idcg


@pytest.fixture
def ndcg_conn(monkeypatch: pytest.MonkeyPatch) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE judge_evaluations (
            run_key VARCHAR,
            recorded_at TIMESTAMP,
            relevance_score DOUBLE
        )
        """
    )
    monkeypatch.setattr(feedback, "_connect", lambda: con)
    return con


def test_compute_ndcg_at_10_hand_computed(ndcg_conn: duckdb.DuckDBPyConnection) -> None:
    # Hand-picked relevance scores (>= 0.8 -> gain 7, >= 0.5 -> gain 3, >= 0.3 -> gain 1)
    relevance = [0.95, 0.20, 0.55, 0.10, 0.85, 0.40, 0.05, 0.75, 0.65, 0.30, 0.50]
    # One (run_key, recorded_at) bucket with 11 graded rows, ranked by score.
    rows = [("r0", "2026-07-22T00:00:00", score) for score in relevance]
    ndcg_conn.executemany("INSERT INTO judge_evaluations VALUES (?, ?, ?)", rows)
    ndcg_conn.commit()

    rows_out = feedback.compute_ndcg_at_10()
    assert len(rows_out) == 1, rows_out
    row = rows_out[0]
    dcg_seen, idcg_seen, ndcg_seen = row["dcg"], row["idcg"], row["ndcg"]

    expected_dcg, expected_idcg = _expected_dcg_idcg(relevance)
    expected_ndcg = round(expected_dcg / expected_idcg, 4) if expected_idcg else 0.0
    assert dcg_seen == pytest.approx(round(expected_dcg, 4), rel=1e-3)
    assert idcg_seen == pytest.approx(round(expected_idcg, 4), rel=1e-3)
    assert ndcg_seen == pytest.approx(expected_ndcg, rel=1e-3)


def test_grade_bins_match_databricks_four_point():
    # Sanity: the in-code bin table must keep the documented thresholds.
    assert _GRADE_BINS == [
        (0.8, 3, 7),
        (0.5, 2, 3),
        (0.3, 1, 1),
        (0.0, 0, 0),
    ]
    # Spot-check the grade/gain mapping for the boundary scores used above.
    assert _grade_for(0.95) == 3 and _gain_for(0.95) == 7
    assert _grade_for(0.55) == 2 and _gain_for(0.55) == 3
    assert _grade_for(0.40) == 1 and _gain_for(0.40) == 1
    assert _grade_for(0.10) == 0 and _gain_for(0.10) == 0
