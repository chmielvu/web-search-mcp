"""Tests for offline graph feedback building, publishing, and index loading."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

import duckdb
import networkx as nx

from kindly_web_search_mcp_server.analytics.duckdb_store import ensure_store_schema
from kindly_web_search_mcp_server.analytics.graph_feedback import (
    GraphBuildConfig,
    GraphBuildError,
    GraphSnapshot,
    build_graph_snapshot,
    load_latest_graph_index,
    publish_graph_snapshot,
)
from kindly_web_search_mcp_server.analytics.writers.core import insert_result_labels


class TestGraphFeedback(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(f"test_graph_feedback_{self._testMethodName}.duckdb")
        self.db_path.unlink(missing_ok=True)
        ensure_store_schema(db_path=str(self.db_path))

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

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
        self.assertEqual(q1_rel["method"], "adamic_adar")
        self.assertGreater(float(str(q1_rel["score"])), 0.0)

        # Ensure cooking pasta has no neighbors
        for q_src, q_tgt in neighbor_pairs:
            self.assertNotEqual(q_src, "cooking pasta")
            self.assertNotEqual(q_tgt, "cooking pasta")

        # Publish snapshot
        publish_graph_snapshot(snapshot, db_path=str(self.db_path))

        # Verify DuckDB tables directly
        con = duckdb.connect(str(self.db_path), read_only=True)
        res_g = con.execute(
            "SELECT COUNT(*) FROM graph_feedback_generations WHERE status = 'ready'"
        ).fetchone()
        res_f = con.execute(
            "SELECT COUNT(*) FROM graph_result_features WHERE generation_id = ?",
            [snapshot.generation_id],
        ).fetchone()
        res_n = con.execute(
            "SELECT COUNT(*) FROM graph_query_neighbors WHERE generation_id = ?",
            [snapshot.generation_id],
        ).fetchone()
        con.close()

        self.assertIsNotNone(res_g)
        self.assertIsNotNone(res_f)
        self.assertIsNotNone(res_n)
        assert res_g is not None and res_f is not None and res_n is not None
        self.assertEqual(res_g[0], 1)
        self.assertEqual(res_f[0], 3)
        self.assertEqual(res_n[0], 2)

        # Load latest index
        index = load_latest_graph_index(db_path=str(self.db_path), max_age_seconds=86400)
        self.assertIsNotNone(index)
        assert index is not None
        self.assertEqual(index.generation_id, snapshot.generation_id)
        self.assertEqual(index.neighbors.get("python tutorial"), ("learn python programming",))
        self.assertEqual(index.neighbors.get("learn python programming"), ("python tutorial",))
        self.assertNotIn("cooking pasta", index.neighbors)

    def test_stale_index_returns_none(self) -> None:
        con = duckdb.connect(str(self.db_path), read_only=False)
        old_time = datetime.now(timezone.utc) - timedelta(days=2)
        gen_id = "gen_old_001"

        con.execute(
            """
            INSERT INTO graph_feedback_generations (
                generation_id, built_at, source_cutoff, label_version, algorithm, status,
                config_json, query_node_count, document_node_count, edge_count,
                shared_document_count, neighbor_row_count
            ) VALUES (
                ?, CAST(? AS TIMESTAMPTZ), CAST(? AS TIMESTAMPTZ), 'v1', 'adamic_adar_birank', 'ready',
                '{}', 2, 2, 2, 1, 2
            )
            """,
            [gen_id, old_time.isoformat(), old_time.isoformat()],
        )
        con.close()

        from kindly_web_search_mcp_server.analytics import graph_feedback

        with graph_feedback._CACHE_LOCK:
            graph_feedback._CACHED_INDEX = None
            graph_feedback._CACHED_AT = 0.0

        index = load_latest_graph_index(db_path=str(self.db_path), max_age_seconds=3600)
        self.assertIsNone(index)

    def test_failed_build_preserves_previous_ready_generation(self) -> None:
        con = duckdb.connect(str(self.db_path), read_only=False)
        now = datetime.now(timezone.utc)
        ready_gen_id = "gen_ready_prev"

        con.execute(
            """
            INSERT INTO graph_feedback_generations (
                generation_id, built_at, source_cutoff, label_version, algorithm, status,
                config_json, query_node_count, document_node_count, edge_count,
                shared_document_count, neighbor_row_count
            ) VALUES (
                ?, CAST(? AS TIMESTAMPTZ), CAST(? AS TIMESTAMPTZ), 'v1', 'adamic_adar_birank', 'ready',
                '{}', 1, 1, 1, 0, 1
            )
            """,
            [ready_gen_id, now.isoformat(), now.isoformat()],
        )
        con.execute(
            """
            INSERT INTO graph_query_neighbors (
                generation_id, query_norm, related_norm, rank, score, method, support_count, built_at
            ) VALUES (?, 'q1', 'q2', 1, 1.0, 'adamic_adar', 2, CAST(? AS TIMESTAMPTZ))
            """,
            [ready_gen_id, now.isoformat()],
        )
        con.close()

        from kindly_web_search_mcp_server.analytics import graph_feedback

        with graph_feedback._CACHE_LOCK:
            graph_feedback._CACHED_INDEX = None
            graph_feedback._CACHED_AT = 0.0

        # Attempt to build from empty result_labels (raises GraphBuildError)
        config = GraphBuildConfig(source_cutoff=now)
        with self.assertRaises(GraphBuildError):
            build_graph_snapshot(db_path=str(self.db_path), config=config)

        # Previous ready generation remains loadable
        index = load_latest_graph_index(db_path=str(self.db_path), max_age_seconds=86400)
        self.assertIsNotNone(index)
        assert index is not None
        self.assertEqual(index.generation_id, ready_gen_id)

    def test_multigraph_projection_rejected(self) -> None:
        mg = nx.MultiGraph()
        mg.add_edge("query:q1", "doc:d1", weight=1.0)
        with self.assertRaises(Exception):
            nx.bipartite.overlap_weighted_projected_graph(mg, {"query:q1"})

    def test_missing_table_returns_none(self) -> None:
        empty_db = Path(f"test_empty_{self._testMethodName}.duckdb")
        empty_db.unlink(missing_ok=True)
        con = duckdb.connect(str(empty_db))
        con.execute("CREATE TABLE dummy (x INT);")
        con.close()

        from kindly_web_search_mcp_server.analytics import graph_feedback

        with graph_feedback._CACHE_LOCK:
            graph_feedback._CACHED_INDEX = None
            graph_feedback._CACHED_AT = 0.0

        res = load_latest_graph_index(db_path=str(empty_db), max_age_seconds=86400)
        self.assertIsNone(res)
        empty_db.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
