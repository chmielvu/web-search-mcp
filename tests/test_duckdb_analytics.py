from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestDuckDBAnalytics(unittest.TestCase):
    def test_append_event_persists_payload(self) -> None:
        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event

        try:
            import duckdb
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
            self.fail(f"duckdb must be available for analytics tests: {exc}")

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        payload = {
            "query": "FastMCP",
            "research_goal": "analytics sink smoke test",
            "provider": "voyage",
            "model": "rerank-2.5",
            "duration_ms": 12.5,
            "input_count": 3,
            "output_count": 2,
            "trace_id": "trace-123",
            "span_id": "span-456",
        }

        append_event("query.rewrite.completed", payload, db_path=str(db_path))

        con = duckdb.connect(str(db_path), read_only=True)
        row = con.execute(
            """
            SELECT event_name, query, research_goal, provider, model, payload_json
            FROM search_events
            """
        ).fetchone()
        con.close()

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row[0], "query.rewrite.completed")
        self.assertEqual(row[1], "FastMCP")
        self.assertEqual(row[2], "analytics sink smoke test")
        self.assertEqual(row[3], "voyage")
        self.assertEqual(row[4], "rerank-2.5")
        self.assertEqual(json.loads(row[5])["span_id"], "span-456")

        if db_path.exists():
            db_path.unlink()


if __name__ == "__main__":
    unittest.main()
