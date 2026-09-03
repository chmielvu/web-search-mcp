"""Tests for read-only replay of persisted graph-expansion decisions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import duckdb

from kindly_web_search_mcp_server.analytics.duckdb_store import ensure_store_schema
from kindly_web_search_mcp_server.analytics.graph_feedback import main
from kindly_web_search_mcp_server.analytics.graph_replay import replay_graph_expansion
from kindly_web_search_mcp_server.analytics.graph_store import (
    GraphSnapshot,
    _CACHE_LOCK,
    _CACHED_INDICES,
    publish_graph_snapshot,
)
from kindly_web_search_mcp_server.analytics.writers.core import insert_result_labels


def _clear_graph_index_cache() -> None:
    with _CACHE_LOCK:
        _CACHED_INDICES.clear()


def _publish_neighbor_index(sqlite_path: str, now: datetime) -> None:
    snapshot = GraphSnapshot(
        generation_id="gen_replay",
        built_at=now,
        source_cutoff=now,
        label_version="v1",
        source_fingerprint="replay-fixture",
        config={
            "scoring_policy_version": "judge_gain_confidence_mean_v1",
            "source_fingerprint": "replay-fixture",
        },
        query_node_count=2,
        document_node_count=2,
        edge_count=2,
        shared_document_count=2,
        neighbors=(
            {
                "query_norm": "base query",
                "related_norm": "related query",
                "rank": 1,
                "score": 1.0,
                "method": "adamic_adar",
                "support_count": 2,
            },
        ),
        result_features=(),
    )
    publish_graph_snapshot(snapshot, sqlite_path=sqlite_path)


def test_replay_uses_persisted_seeds_and_current_fallback(tmp_path: Path) -> None:
    db_path = str(tmp_path / "replay.duckdb")
    sqlite_path = str(tmp_path / "replay.graph.sqlite")
    ensure_store_schema(db_path=db_path)
    now = datetime.now(timezone.utc)

    con = duckdb.connect(db_path, read_only=False)
    try:
        con.execute(
            """
            INSERT INTO search_runs (
                run_key, query, normalized_query, payload_json, final_result_count,
                provider_count, duration_ms, recorded_at
            )
            VALUES
                ('run-with-seeds', 'base query', 'base query', ?, 2, 2, 100.0, ?),
                ('run-without-seeds', 'other query', 'other query', ?, 1, 1, 200.0, ?)
            """,
            [
                json.dumps(
                    {
                        "seed_queries": ["base query", "caller seed"],
                        "graph_expansion": {"status": "applied"},
                    }
                ),
                now,
                json.dumps({"graph_expansion": {"status": "disabled"}}),
                now,
            ],
        )
        con.execute(
            """
            INSERT INTO final_results (run_key, rank, domain, recorded_at)
            VALUES
                ('run-with-seeds', 1, 'example.com', ?),
                ('run-with-seeds', 2, 'example.org', ?),
                ('run-without-seeds', 1, 'example.com', ?)
            """,
            [now, now, now],
        )
    finally:
        con.close()

    insert_result_labels(
        [
            {
                "run_key": "run-with-seeds",
                "position": 0,
                "label": 1.0,
                "canonical_result_id": "result-1",
                "source": "llm_judge",
                "annotator_id": "judge",
                "rubric_version": "v1",
                "raw_url": "https://example.com/one",
                "recorded_at": now,
            }
        ],
        db_path=db_path,
        sync=True,
    )

    _publish_neighbor_index(sqlite_path, now)
    _clear_graph_index_cache()

    report = replay_graph_expansion(db_path=db_path, sqlite_path=sqlite_path)

    assert report.fallback_seed_count == 1
    assert [row.run_key for row in report.rows] == ["run-with-seeds", "run-without-seeds"]

    applied, no_match = report.rows
    assert applied.base_seed_queries == ("base query", "caller seed")
    assert applied.decision.status == "applied"
    assert applied.decision.effective_seed_queries == (
        "base query",
        "caller seed",
        "related query",
    )
    assert no_match.base_seed_queries == ("other query",)
    assert no_match.decision.status == "no_match"

    assert report.to_dict()["decision_counts"] == {"applied": 1, "no_match": 1}
    metrics = report.to_dict()["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["branch_cardinality"] == {"6": 2}
    assert metrics["eligible_query_coverage"] == 1.0
    assert metrics["related_query_coverage"] == 0.5
    assert metrics["candidate_support_distribution"] == {2: 1}
    outcome_metrics = report.to_dict()["outcome_metrics"]
    assert isinstance(outcome_metrics, dict)
    treatment = outcome_metrics["treatment"]
    control = outcome_metrics["control"]
    assert isinstance(treatment, dict)
    assert isinstance(control, dict)
    assert treatment["run_count"] == 1
    assert treatment["mean_result_quality"] == 1.0
    assert treatment["ndcg_at_10"] == 1.0
    assert treatment["mrr_at_10"] == 1.0
    assert treatment["mean_top10_unique_domain_count"] == 2.0
    assert control["run_count"] == 1
    assert control["judged_run_count"] == 0


def test_replay_returns_empty_report_for_missing_database(tmp_path: Path) -> None:
    report = replay_graph_expansion(db_path=str(tmp_path / "missing.duckdb"))

    assert report.rows == ()
    assert report.fallback_seed_count == 0
    assert report.decision_counts == {}


def test_replay_cli_reports_missing_database(tmp_path: Path, capsys) -> None:
    exit_code = main(["replay", "--db-path", str(tmp_path / "missing.duckdb")])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "decision_counts": {},
        "outcome_metrics": {},
        "fallback_seed_count": 0,
        "metrics": {
            "branch_cardinality": {"6": 0},
            "candidate_support_distribution": {},
            "effective_seed_distribution": {},
            "eligible_query_coverage": 0.0,
            "head_tail_query_split": {"head": 0, "tail": 0},
            "no_match_stale_error_rate": 0.0,
            "prompt_size_delta_chars": 0,
            "related_query_coverage": 0.0,
        },
        "rows": [],
        "runs_replayed": 0,
    }
