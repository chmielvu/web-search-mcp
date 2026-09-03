"""Tests for offline graph feedback building, publishing, and index loading."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

import duckdb
import networkx as nx
import sqlite3

from kindly_web_search_mcp_server.analytics.duckdb_store import ensure_store_schema
from kindly_web_search_mcp_server.analytics.graph_feedback import (
    GraphBuildConfig,
    GraphBuildError,
    build_graph_snapshot,
)
from kindly_web_search_mcp_server.analytics.graph_store import (
    GraphSnapshot,
    _CACHE_LOCK,
    _CACHED_INDICES,
    load_latest_graph_index,
    publish_graph_snapshot,
)
from kindly_web_search_mcp_server.analytics.writers.core import insert_result_labels


class TestGraphFeedback(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(f"test_graph_feedback_{self._testMethodName}.duckdb")
        self.sqlite_path = self.db_path.with_suffix(".graph.sqlite")
        self.db_path.unlink(missing_ok=True)
        self.sqlite_path.unlink(missing_ok=True)
        ensure_store_schema(db_path=str(self.db_path))

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)
        self.sqlite_path.unlink(missing_ok=True)

    def test_build_publish_load_happy_path(self) -> None:
        con = duckdb.connect(str(self.db_path), read_only=False)
        now = datetime.now(timezone.utc)

        # Seed 3 search_runs:
        # q1: "python tutorial"
        # q2: "learn python programming"
        # q3: "cooking pasta"
        con.execute(
            """
            INSERT INTO search_runs (run_key, query, normalized_query, recorded_at) VALUES
            ('rk1', 'python tutorial', 'python tutorial', ?),
            ('rk2', 'learn python programming', 'learn python programming', ?),
            ('rk3', 'cooking pasta', 'cooking pasta', ?)
            """,
            [now, now, now],
        )
        con.close()

        # Seed result_labels:
        # q1 shares doc_py1 and doc_py2 with q2
        # q3 has doc_pasta1
        labels = [
            # q1 docs
            {
                "run_key": "rk1",
                "position": 0,
                "label": 1.0,
                "canonical_result_id": "doc_py1",
                "raw_url": "https://example.com/py1",
                "source": "llm_judge",
                "rubric_version": "v1",
                "recorded_at": now,
            },
            {
                "run_key": "rk1",
                "position": 1,
                "label": 0.8,
                "canonical_result_id": "doc_py2",
                "raw_url": "https://example.com/py2",
                "source": "llm_judge",
                "rubric_version": "v1",
                "recorded_at": now,
            },
            # q2 docs (shares py1 and py2)
            {
                "run_key": "rk2",
                "position": 0,
                "label": 1.0,
                "canonical_result_id": "doc_py1",
                "raw_url": "https://example.com/py1",
                "source": "llm_judge",
                "rubric_version": "v1",
                "recorded_at": now,
            },
            {
                "run_key": "rk2",
                "position": 1,
                "label": 0.9,
                "canonical_result_id": "doc_py2",
                "raw_url": "https://example.com/py2",
                "source": "llm_judge",
                "rubric_version": "v1",
                "recorded_at": now,
            },
            # q3 docs (disjoint)
            {
                "run_key": "rk3",
                "position": 0,
                "label": 1.0,
                "canonical_result_id": "doc_pasta1",
                "raw_url": "https://example.com/pasta1",
                "source": "llm_judge",
                "rubric_version": "v1",
                "recorded_at": now,
            },
        ]
        insert_result_labels(labels, db_path=str(self.db_path), sync=True)

        config = GraphBuildConfig(
            source_cutoff=now,
            label_version="v1",
            min_shared_documents=2,
            max_related_queries=5,
        )
        snapshot = build_graph_snapshot(db_path=str(self.db_path), config=config)

        self.assertIsInstance(snapshot, GraphSnapshot)
        self.assertEqual(snapshot.query_node_count, 3)
        self.assertEqual(snapshot.document_node_count, 3)
        self.assertEqual(snapshot.edge_count, 5)
        self.assertEqual(snapshot.shared_document_count, 2)

        # Check result_features: all 3 docs should have scores
        doc_features = {str(f["canonical_result_id"]): f for f in snapshot.result_features}
        self.assertEqual(len(doc_features), 3)
        self.assertIn("doc_py1", doc_features)
        self.assertIn("doc_py2", doc_features)
        self.assertIn("doc_pasta1", doc_features)
        for feat in doc_features.values():
            self.assertGreater(float(str(feat["birank_score"])), 0.0)
            self.assertGreater(float(str(feat["pagerank_score"])), 0.0)
            self.assertGreater(float(str(feat["weighted_degree"])), 0.0)

        # Check neighbors: q1 and q2 should be related with support_count=2
        # q3 should NOT be related to q1 or q2
        neighbor_pairs = {
            (str(n["query_norm"]), str(n["related_norm"])): n for n in snapshot.neighbors
        }
        self.assertIn(("python tutorial", "learn python programming"), neighbor_pairs)
        self.assertIn(("learn python programming", "python tutorial"), neighbor_pairs)

        q1_rel = neighbor_pairs[("python tutorial", "learn python programming")]
        self.assertEqual(q1_rel["support_count"], 2)
        self.assertEqual(q1_rel["rank"], 1)
        self.assertEqual(q1_rel["method"], "adamic_adar_exposure_topology")
        self.assertGreater(float(str(q1_rel["score"])), 0.0)

        # Ensure cooking pasta has no neighbors
        for q_src, q_tgt in neighbor_pairs:
            self.assertNotEqual(q_src, "cooking pasta")
            self.assertNotEqual(q_tgt, "cooking pasta")

        publish_graph_snapshot(snapshot, sqlite_path=str(self.sqlite_path))

        connection = sqlite3.connect(str(self.sqlite_path))
        try:
            ready_count = connection.execute(
                "SELECT COUNT(*) FROM graph_generations WHERE status = 'ready'"
            ).fetchone()
            feature_count = connection.execute(
                "SELECT COUNT(*) FROM graph_result_features WHERE generation_id = ?",
                [snapshot.generation_id],
            ).fetchone()
            neighbor_count = connection.execute(
                "SELECT COUNT(*) FROM graph_query_neighbors WHERE generation_id = ?",
                [snapshot.generation_id],
            ).fetchone()
        finally:
            connection.close()

        assert ready_count is not None and feature_count is not None and neighbor_count is not None
        self.assertEqual(ready_count[0], 1)
        self.assertEqual(feature_count[0], 3)
        self.assertEqual(neighbor_count[0], 2)

        index = load_latest_graph_index(sqlite_path=str(self.sqlite_path), max_age_seconds=86400)
        self.assertIsNotNone(index)
        assert index is not None
        self.assertEqual(index.generation_id, snapshot.generation_id)
        self.assertEqual(index.neighbors.get("python tutorial"), ("learn python programming",))
        self.assertEqual(index.neighbors.get("learn python programming"), ("python tutorial",))
        self.assertNotIn("cooking pasta", index.neighbors)

    def test_stale_index_returns_none(self) -> None:
        old_time = datetime.now(timezone.utc) - timedelta(days=2)
        snapshot = GraphSnapshot(
            generation_id="gen_old_001",
            built_at=old_time,
            source_cutoff=old_time,
            label_version="v1",
            source_fingerprint="old-fingerprint",
            config={
                "scoring_policy_version": "judge_gain_confidence_mean_v1",
                "source_fingerprint": "old-fingerprint",
            },
            query_node_count=1,
            document_node_count=1,
            edge_count=1,
            shared_document_count=0,
            neighbors=(),
            result_features=(),
        )
        publish_graph_snapshot(snapshot, sqlite_path=str(self.sqlite_path))
        _CACHED_INDICES.clear()

        index = load_latest_graph_index(sqlite_path=str(self.sqlite_path), max_age_seconds=3600)
        self.assertIsNone(index)

    def test_failed_build_preserves_previous_ready_generation(self) -> None:
        now = datetime.now(timezone.utc)
        ready_gen_id = "gen_ready_prev"
        snapshot = GraphSnapshot(
            generation_id=ready_gen_id,
            built_at=now,
            source_cutoff=now,
            label_version="v1",
            source_fingerprint="previous",
            config={
                "scoring_policy_version": "judge_gain_confidence_mean_v1",
                "source_fingerprint": "previous",
            },
            query_node_count=1,
            document_node_count=1,
            edge_count=1,
            shared_document_count=0,
            neighbors=(
                {
                    "query_norm": "q1",
                    "related_norm": "q2",
                    "rank": 1,
                    "score": 1.0,
                    "method": "adamic_adar",
                    "support_count": 2,
                },
            ),
            result_features=(),
        )
        publish_graph_snapshot(snapshot, sqlite_path=str(self.sqlite_path))
        _CACHED_INDICES.clear()

        config = GraphBuildConfig(source_cutoff=now)
        with self.assertRaises(GraphBuildError):
            build_graph_snapshot(db_path=str(self.db_path), config=config)

        index = load_latest_graph_index(sqlite_path=str(self.sqlite_path), max_age_seconds=86400)
        self.assertIsNotNone(index)
        assert index is not None
        self.assertEqual(index.generation_id, ready_gen_id)

    def test_multigraph_projection_rejected(self) -> None:
        mg = nx.MultiGraph()
        mg.add_edge("query:q1", "doc:d1", weight=1.0)
        with self.assertRaises(Exception):
            nx.bipartite.overlap_weighted_projected_graph(mg, {"query:q1"})


    def test_index_cache_is_scoped_by_sqlite_path(self) -> None:
        second_sqlite = self.sqlite_path.with_name("second.graph.sqlite")
        second_sqlite.unlink(missing_ok=True)
        now = datetime.now(timezone.utc)

        def snapshot(generation_id: str, query: str, related: str) -> GraphSnapshot:
            fingerprint = f"fingerprint-{generation_id}"
            return GraphSnapshot(
                generation_id=generation_id,
                built_at=now,
                source_cutoff=now,
                label_version="v1",
                source_fingerprint=fingerprint,
                config={
                    "scoring_policy_version": "judge_gain_confidence_mean_v1",
                    "source_fingerprint": fingerprint,
                },
                query_node_count=2,
                document_node_count=2,
                edge_count=2,
                shared_document_count=2,
                neighbors=(
                    {
                        "query_norm": query,
                        "related_norm": related,
                        "rank": 1,
                        "score": 1.0,
                        "method": "adamic_adar",
                        "support_count": 2,
                    },
                ),
                result_features=(),
            )

        try:
            publish_graph_snapshot(
                snapshot("gen-first", "first", "first-related"),
                sqlite_path=str(self.sqlite_path),
            )
            publish_graph_snapshot(
                snapshot("gen-second", "second", "second-related"),
                sqlite_path=str(second_sqlite),
            )
            with _CACHE_LOCK:
                _CACHED_INDICES.clear()
            first = load_latest_graph_index(
                sqlite_path=str(self.sqlite_path), max_age_seconds=3600
            )
            second = load_latest_graph_index(sqlite_path=str(second_sqlite), max_age_seconds=3600)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            assert first is not None and second is not None
            self.assertEqual(first.generation_id, "gen-first")
            self.assertEqual(second.generation_id, "gen-second")
            self.assertEqual(first.neighbors, {"first": ("first-related",)})
            self.assertEqual(second.neighbors, {"second": ("second-related",)})
        finally:
            second_sqlite.unlink(missing_ok=True)

    def test_missing_sqlite_artifact_returns_none(self) -> None:
        missing_sqlite = self.sqlite_path.with_name("missing.graph.sqlite")
        missing_sqlite.unlink(missing_ok=True)
        with _CACHE_LOCK:
            _CACHED_INDICES.clear()
        self.assertIsNone(
            load_latest_graph_index(sqlite_path=str(missing_sqlite), max_age_seconds=86400)
        )



if __name__ == "__main__":
    unittest.main()
