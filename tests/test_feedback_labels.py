"""Tests for offline judge label materialization."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import unittest

import duckdb

from kindly_web_search_mcp_server.analytics.duckdb_store import ensure_store_schema
from kindly_web_search_mcp_server.analytics.feedback_labels import (
    materialize_result_labels,
    parse_result_quality_payload,
)
from kindly_web_search_mcp_server.analytics.observability_ids import _canonical_result_id


class TestFeedbackLabels(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(f"test_feedback_labels_{self._testMethodName}.duckdb")
        self.db_path.unlink(missing_ok=True)
        ensure_store_schema(db_path=str(self.db_path))

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_parse_result_quality_payload(self) -> None:
        # Valid payload with intent_match=True
        payload: dict[str, Any] = {
            "facet": "result_quality",
            "schema_version": "v1",
            "parsed": {"intent_match": True, "informativeness": 4, "confidence": 3},
        }
        res = parse_result_quality_payload(payload)
        self.assertIsNotNone(res)
        assert res is not None
        self.assertTrue(res.intent_match)
        self.assertEqual(res.informativeness, 4)
        self.assertEqual(res.confidence, 3)
        self.assertEqual(res.label, 1.0)

        # JSON string format
        res_str = parse_result_quality_payload(json.dumps(payload))
        self.assertEqual(res, res_str)

        # intent_match=False gives label=0.0
        payload_false: dict[str, Any] = {
            "parsed": {"intent_match": False, "informativeness": 4, "confidence": 3}
        }
        res_f = parse_result_quality_payload(payload_false)
        self.assertIsNotNone(res_f)
        assert res_f is not None
        self.assertEqual(res_f.label, 0.0)

        # informativeness scale mapping: 1->0.0, 2->1/3, 3->2/3, 4->1.0
        p1 = parse_result_quality_payload(
            {"parsed": {"intent_match": True, "informativeness": 1, "confidence": 4}}
        )
        p2 = parse_result_quality_payload(
            {"parsed": {"intent_match": True, "informativeness": 2, "confidence": 4}}
        )
        p3 = parse_result_quality_payload(
            {"parsed": {"intent_match": True, "informativeness": 3, "confidence": 4}}
        )
        assert p1 and p2 and p3
        self.assertAlmostEqual(p1.label, 0.0)
        self.assertAlmostEqual(p2.label, 1.0 / 3.0)
        self.assertAlmostEqual(p3.label, 2.0 / 3.0)

        # Rejection cases
        self.assertIsNone(parse_result_quality_payload(None))
        self.assertIsNone(parse_result_quality_payload(""))
        self.assertIsNone(parse_result_quality_payload("invalid json"))
        self.assertIsNone(parse_result_quality_payload({}))
        self.assertIsNone(parse_result_quality_payload({"parsed": None}))
        self.assertIsNone(
            parse_result_quality_payload({"parsed": {"intent_match": "true"}})
        )  # string bool
        self.assertIsNone(
            parse_result_quality_payload(
                {"parsed": {"intent_match": True, "informativeness": 0, "confidence": 3}}
            )
        )  # out of domain
        self.assertIsNone(
            parse_result_quality_payload(
                {"parsed": {"intent_match": True, "informativeness": 5, "confidence": 3}}
            )
        )  # out of domain
        self.assertIsNone(
            parse_result_quality_payload(
                {"parsed": {"intent_match": True, "informativeness": 3, "confidence": 0}}
            )
        )  # out of domain
        self.assertIsNone(
            parse_result_quality_payload(
                {"parsed": {"intent_match": True, "informativeness": 3, "confidence": 5}}
            )
        )  # out of domain
        self.assertIsNone(
            parse_result_quality_payload(
                {"parsed": {"intent_match": True, "informativeness": True, "confidence": 3}}
            )
        )  # bool int

    def test_materialize_happy_path(self) -> None:
        con = duckdb.connect(str(self.db_path), read_only=False)
        rk = "rk_test_happy_001"
        link = "https://example.com/article"
        now = datetime.now(timezone.utc)

        # Seed search_runs
        con.execute(
            """
            INSERT INTO search_runs (run_key, query, normalized_query, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            [rk, "test query", "test query", now],
        )

        # Seed final_results with rank=1 (1-based) and NULL canonical_result_id
        con.execute(
            """
            INSERT INTO final_results (run_key, rank, link, title, canonical_result_id, recorded_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            [rk, 1, link, "Example Article", now],
        )

        # Seed successful llm_judgments row
        payload = json.dumps(
            {
                "facet": "result_quality",
                "schema_version": "v1",
                "parsed": {"intent_match": True, "informativeness": 4, "confidence": 3},
            }
        )
        con.execute(
            """
            INSERT INTO llm_judgments (
                run_key, judgment_kind, judgment_target, prompt_name, model_name,
                verdict, status, payload_json, rubric_version, recorded_at
            )
            VALUES (?, 'result_quality', ?, 'judge_result_quality', 'gemini-2.5', 'GOOD', 'success', ?, 'v1', ?)
            """,
            [rk, link, payload, now],
        )
        con.close()

        report = materialize_result_labels(db_path=str(self.db_path))
        self.assertEqual(report.inspected, 1)
        self.assertEqual(report.accepted, 1)
        self.assertEqual(report.rejected_payload, 0)
        self.assertEqual(report.rejected_target, 0)
        self.assertEqual(report.rejected_position, 0)
        self.assertEqual(report.duplicate_candidates, 0)
        self.assertEqual(report.submitted, 1)

        # Verify inserted result_labels row
        con = duckdb.connect(str(self.db_path), read_only=True)
        rows = con.execute(
            """
            SELECT
                label_id,
                strftime(recorded_at, '%Y-%m-%dT%H:%M:%S.%fZ') AS recorded_at,
                run_key,
                position,
                stage,
                label,
                canonical_result_id,
                raw_url,
                source,
                annotator_id,
                rubric_version,
                discounted_gain,
                notes,
                payload_json
            FROM result_labels
            WHERE run_key = ?
            """,
            [rk],
        ).fetchall()
        con.close()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        col_names = [
            "label_id",
            "recorded_at",
            "run_key",
            "position",
            "stage",
            "label",
            "canonical_result_id",
            "raw_url",
            "source",
            "annotator_id",
            "rubric_version",
            "discounted_gain",
            "notes",
            "payload_json",
        ]
        row_dict = dict(zip(col_names, row))

        self.assertEqual(row_dict["run_key"], rk)
        self.assertEqual(row_dict["position"], 0)  # 1-based rank=1 -> 0-based position=0
        self.assertEqual(row_dict["stage"], "final")
        self.assertEqual(row_dict["source"], "llm_judge")
        self.assertEqual(row_dict["annotator_id"], "gemini-2.5")
        self.assertEqual(row_dict["rubric_version"], "v1")
        self.assertEqual(row_dict["label"], 1.0)
        self.assertEqual(row_dict["canonical_result_id"], _canonical_result_id(link))
        self.assertEqual(row_dict["raw_url"], link)
        self.assertEqual(row_dict["discounted_gain"], 1.0)  # label / log2(0 + 2) = 1.0 / 1.0 = 1.0

        # Verify payload_json contents
        p_json = json.loads(str(row_dict["payload_json"]))
        self.assertEqual(p_json["parsed"]["intent_match"], True)
        self.assertEqual(p_json["parsed"]["informativeness"], 4)
        self.assertEqual(p_json["parsed"]["confidence"], 3)
        self.assertEqual(p_json["judge_label"], 1.0)
        self.assertEqual(p_json["confidence_fraction"], 0.75)
        self.assertEqual(p_json["model_name"], "gemini-2.5")

    def test_rejections_and_counting(self) -> None:
        con = duckdb.connect(str(self.db_path), read_only=False)
        now = datetime.now(timezone.utc)

        rk = "rk_test_rejections"
        con.execute(
            "INSERT INTO search_runs (run_key, query, normalized_query, recorded_at) VALUES (?, ?, ?, ?)",
            [rk, "q", "q", now],
        )

        # final_results rows
        con.execute(
            """
            INSERT INTO final_results (run_key, rank, link, title, canonical_result_id, recorded_at) VALUES
            (?, 1, 'https://example.com/target1', 'T1', 'cid1', ?),
            (?, NULL, 'https://example.com/no_rank', 'NoRank', 'cid2', ?),
            (?, 0, 'https://example.com/zero_rank', 'ZeroRank', 'cid3', ?),
            (?, 2, 'https://example.com/dup_url?a=1', 'Ambiguous 1', 'cid4', ?),
            (?, 3, 'https://example.com/dup_url?b=2', 'Ambiguous 2', 'cid5', ?)
            """,
            [rk, now, rk, now, rk, now, rk, now, rk, now],
        )

        valid_payload = json.dumps(
            {"parsed": {"intent_match": True, "informativeness": 4, "confidence": 4}}
        )
        malformed_payload = json.dumps({"parsed": {"intent_match": "not_a_bool"}})

        con.execute(
            """
            INSERT INTO llm_judgments (
                run_key, judgment_kind, judgment_target, prompt_name, model_name,
                verdict, status, payload_json, rubric_version, recorded_at
            ) VALUES
            -- 1. Malformed payload
            (?, 'result_quality', 'https://example.com/target1', 'p', 'm', 'v', 'success', ?, 'v1', ?),
            -- 2. Blank target
            (?, 'result_quality', '', 'p', 'm', 'v', 'success', ?, 'v1', ?),
            -- 3. Missing final URL / zero matches
            (?, 'result_quality', 'https://example.com/unmatched', 'p', 'm', 'v', 'success', ?, 'v1', ?),
            -- 4. Ambiguous canonical match (both https://example.com/dup_url?a=1 and ?b=2 canonicalize to https://example.com/dup_url)
            (?, 'result_quality', 'https://example.com/dup_url', 'p', 'm', 'v', 'success', ?, 'v1', ?),
            -- 5. Missing rank (rank=NULL)
            (?, 'result_quality', 'https://example.com/no_rank', 'p', 'm', 'v', 'success', ?, 'v1', ?),
            -- 6. Invalid rank (rank=0)
            (?, 'result_quality', 'https://example.com/zero_rank', 'p', 'm', 'v', 'success', ?, 'v1', ?),
            -- 7. Error status (should be filtered by SQL WHERE status = 'success')
            (?, 'result_quality', 'https://example.com/target1', 'p', 'm', 'v', 'error', ?, 'v1', ?)
            """,
            [
                rk,
                malformed_payload,
                now,
                rk,
                valid_payload,
                now,
                rk,
                valid_payload,
                now,
                rk,
                valid_payload,
                now,
                rk,
                valid_payload,
                now,
                rk,
                valid_payload,
                now,
                rk,
                valid_payload,
                now,
            ],
        )
        con.close()

        report = materialize_result_labels(db_path=str(self.db_path))
        # Note: error status row is excluded by SQL query filter, so inspected=6
        self.assertEqual(report.inspected, 6)
        self.assertEqual(report.accepted, 0)
        self.assertEqual(report.rejected_payload, 1)
        self.assertEqual(report.rejected_target, 3)  # blank target, unmatched, ambiguous
        self.assertEqual(report.rejected_position, 2)  # rank=NULL, rank=0
        self.assertEqual(report.submitted, 0)

    def test_idempotency_on_rerun(self) -> None:
        con = duckdb.connect(str(self.db_path), read_only=False)
        rk = "rk_test_idempotency"
        link = "https://example.com/page"
        now = datetime.now(timezone.utc)

        con.execute(
            "INSERT INTO search_runs (run_key, query, normalized_query, recorded_at) VALUES (?, ?, ?, ?)",
            [rk, "q", "q", now],
        )
        con.execute(
            "INSERT INTO final_results (run_key, rank, link, title, canonical_result_id, recorded_at) VALUES (?, 1, ?, 'T', 'cid_page', ?)",
            [rk, link, now],
        )
        payload = json.dumps(
            {"parsed": {"intent_match": True, "informativeness": 3, "confidence": 4}}
        )
        con.execute(
            """
            INSERT INTO llm_judgments (
                run_key, judgment_kind, judgment_target, prompt_name, model_name,
                verdict, status, payload_json, rubric_version, recorded_at
            ) VALUES (?, 'result_quality', ?, 'p', 'm', 'v', 'success', ?, 'v1', ?)
            """,
            [rk, link, payload, now],
        )
        con.close()

        # Run 1
        rep1 = materialize_result_labels(db_path=str(self.db_path))
        self.assertEqual(rep1.submitted, 1)

        con = duckdb.connect(str(self.db_path), read_only=True)
        res1 = con.execute("SELECT COUNT(*) FROM result_labels WHERE run_key = ?", [rk]).fetchone()
        con.close()
        self.assertIsNotNone(res1)
        assert res1 is not None
        self.assertEqual(res1[0], 1)

        # Run 2
        rep2 = materialize_result_labels(db_path=str(self.db_path))
        self.assertEqual(rep2.submitted, 1)

        con = duckdb.connect(str(self.db_path), read_only=True)
        res2 = con.execute("SELECT COUNT(*) FROM result_labels WHERE run_key = ?", [rk]).fetchone()
        con.close()
        self.assertIsNotNone(res2)
        assert res2 is not None
        self.assertEqual(res2[0], 1)  # ON CONFLICT DO NOTHING kept row count at 1



    def test_materializes_latest_valid_observation_per_model_deterministically(self) -> None:
        con = duckdb.connect(str(self.db_path), read_only=False)
        run_key = "rk_test_latest_observation"
        result_url = "https://example.com/target"
        observed_target = "https://example.com/target?utm_source=judge"
        initial = datetime(2026, 1, 1, tzinfo=timezone.utc)
        con.execute(
            "INSERT INTO search_runs (run_key, query, normalized_query, recorded_at) VALUES (?, ?, ?, ?)",
            [run_key, "query", "query", initial],
        )
        con.execute(
            """
            INSERT INTO final_results
                (run_key, rank, link, title, canonical_result_id, recorded_at)
            VALUES (?, 1, ?, 'Target', 'target-canonical-id', ?)
            """,
            [run_key, result_url, initial],
        )

        def payload(informativeness: int, confidence: int) -> str:
            return json.dumps(
                {
                    "parsed": {
                        "intent_match": True,
                        "informativeness": informativeness,
                        "confidence": confidence,
                    }
                }
            )

        judgments = [
            ("same-model", payload(4, 4), initial),
            ("same-model", payload(2, 4), initial.replace(second=1)),
            ("invalid-latest", payload(3, 4), initial),
            ("invalid-latest", payload(4, 5), initial.replace(second=1)),
            ("other-model", payload(4, 4), initial.replace(second=2)),
            ("equal-timestamp", payload(2, 4), initial.replace(second=3)),
            ("equal-timestamp", payload(4, 4), initial.replace(second=3)),
        ]

        def insert_judgments(rows: list[tuple[str, str, datetime]]) -> None:
            for model_name, raw_payload, recorded_at in rows:
                con.execute(
                    """
                    INSERT INTO llm_judgments (
                        run_key, judgment_kind, judgment_target, prompt_name, model_name,
                        verdict, status, payload_json, rubric_version, recorded_at
                    )
                    VALUES (?, 'result_quality', ?, 'p', ?, 'v', 'success', ?, 'v1', ?)
                    """,
                    [run_key, observed_target, model_name, raw_payload, recorded_at],
                )

        insert_judgments(judgments)
        con.close()

        report = materialize_result_labels(db_path=str(self.db_path))
        self.assertEqual(report.inspected, len(judgments))
        self.assertEqual(report.accepted, 4)
        self.assertEqual(report.rejected_payload, 1)
        self.assertEqual(report.duplicate_candidates, 2)
        self.assertEqual(report.submitted, 4)

        def materialized_labels() -> dict[str, tuple[float, str]]:
            read_con = duckdb.connect(str(self.db_path), read_only=True)
            try:
                return {
                    annotator_id: (label, payload_json)
                    for annotator_id, label, payload_json in read_con.execute(
                        """
                        SELECT annotator_id, label, payload_json
                        FROM result_labels
                        WHERE run_key = ?
                        ORDER BY annotator_id
                        """,
                        [run_key],
                    ).fetchall()
                }
            finally:
                read_con.close()

        first_materialization = materialized_labels()
        self.assertEqual(first_materialization["same-model"][0], 1.0 / 3.0)
        self.assertEqual(first_materialization["invalid-latest"][0], 2.0 / 3.0)
        self.assertEqual(first_materialization["other-model"][0], 1.0)
        self.assertEqual(len(first_materialization), 4)

        con = duckdb.connect(str(self.db_path), read_only=False)
        con.execute("DELETE FROM result_labels WHERE run_key = ?", [run_key])
        con.execute("DELETE FROM llm_judgments WHERE run_key = ?", [run_key])
        insert_judgments(list(reversed(judgments)))
        con.close()

        second_report = materialize_result_labels(db_path=str(self.db_path))
        self.assertEqual(second_report.submitted, 4)
        self.assertEqual(materialized_labels(), first_materialization)

if __name__ == "__main__":
    unittest.main()
