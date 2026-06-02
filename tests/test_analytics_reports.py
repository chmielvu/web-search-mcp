from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestAnalyticsReports(unittest.TestCase):
    def test_ensure_local_views_installs_base_and_candidate_views(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event
        from kindly_web_search_mcp_server.analytics.views import ensure_local_views

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        append_event(
            "provider.search.result",
            {
                "provider_name": "searxng",
                "query": "duckdb",
                "results": [
                    {
                        "title": "DuckDB docs",
                        "link": "https://duckdb.org/docs",
                        "snippet": "DuckDB documentation",
                        "domain": "duckdb.org",
                        "providers": ["searxng"],
                        "provider_count": 1,
                        "score": 0.9,
                    }
                ],
            },
            db_path=str(db_path),
        )

        ensure_local_views(db_path=str(db_path))

        con = duckdb.connect(str(db_path), read_only=True)
        provider_row = con.execute(
            "SELECT provider, title FROM vw_provider_results"
        ).fetchone()
        events_row = con.execute(
            "SELECT provider FROM vw_events WHERE event_name = 'provider.search.result'"
        ).fetchone()
        candidate_count = con.execute(
            "SELECT COUNT(*) FROM vw_candidate_survival"
        ).fetchone()[0]
        con.close()

        self.assertEqual(provider_row, ("searxng", "DuckDB docs"))
        self.assertEqual(events_row, ("searxng",))
        self.assertEqual(candidate_count, 1)

        if db_path.exists():
            db_path.unlink()

    def test_provider_performance_report_aggregates_counts(self) -> None:
        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event
        from kindly_web_search_mcp_server.analytics.reports import provider_performance

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        append_event(
            "provider.search.result",
            {
                "provider_name": "searxng",
                "duration_ms": 12.0,
                "output_count": 3,
                "results": [],
            },
            db_path=str(db_path),
        )
        append_event(
            "provider.search.error",
            {
                "provider_name": "searxng",
                "duration_ms": 30.0,
                "error_type": "TimeoutError",
            },
            db_path=str(db_path),
        )

        table = provider_performance(days=30, db_path=str(db_path))
        rows = table.to_pylist()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"], "searxng")
        self.assertEqual(rows[0]["calls"], 2)
        self.assertEqual(rows[0]["result_events"], 1)
        self.assertEqual(rows[0]["error_events"], 1)

        if db_path.exists():
            db_path.unlink()

    def test_analytics_report_cli_prints_json(self) -> None:
        from kindly_web_search_mcp_server import cli
        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        append_event(
            "provider.search.result",
            {
                "provider_name": "searxng",
                "duration_ms": 12.0,
                "output_count": 3,
                "results": [],
            },
            db_path=str(db_path),
        )

        stdout = io.StringIO()
        with patch("sys.stdout", new=stdout):
            cli.main(
                [
                    "analytics-report",
                    "--report",
                    "provider-performance",
                    "--days",
                    "7",
                    "--duckdb-path",
                    str(db_path),
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["report"], "provider-performance")
        self.assertEqual(payload["days"], 7)
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["provider"], "searxng")

        if db_path.exists():
            db_path.unlink()

    def test_eval_quality_summary_report_aggregates_raw_tables(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.evals import ensure_eval_tables
        from kindly_web_search_mcp_server.analytics.reports import (
            eval_quality_summary,
        )

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()
        duckdb.connect(str(db_path)).close()

        ensure_eval_tables(db_path=str(db_path))
        con = duckdb.connect(str(db_path))
        con.execute(
            """
            INSERT INTO eval_runs VALUES (
                'run-1',
                CURRENT_TIMESTAMP,
                'suite-a',
                'tester',
                'dataset-a',
                'prompt-v1',
                '{"notes":"ok"}',
                '{"suite":"suite-a"}'
            )
            """
        )
        con.execute(
            """
            INSERT INTO eval_cases VALUES (
                'case-1',
                'run-1',
                CURRENT_TIMESTAMP,
                'web_search',
                'fetch quality',
                'good fetch',
                '{"expected":"markdown"}',
                '{"label":"smoke"}',
                'trace-1',
                'run-1',
                '{"case":"case-1"}'
            )
            """
        )
        con.execute(
            """
            INSERT INTO eval_observations VALUES (
                'obs-1',
                'run-1',
                'case-1',
                CURRENT_TIMESTAMP,
                'tool.web_search.response',
                'run-1',
                0.8,
                'pass',
                '{"note":"ok"}',
                '{"obs":"obs-1"}'
            )
            """
        )
        con.execute(
            """
            INSERT INTO llm_quality_scores VALUES (
                'score-1',
                'run-1',
                'case-1',
                CURRENT_TIMESTAMP,
                'clarity',
                0.9,
                'gpt-4.1',
                'good answer',
                '{"score":"score-1"}'
            )
            """
        )
        con.close()

        table = eval_quality_summary(days=30, db_path=str(db_path))
        rows = table.to_pylist()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["suite_name"], "suite-a")
        self.assertEqual(rows[0]["target_tool"], "web_search")
        self.assertEqual(rows[0]["cases"], 1)
        self.assertEqual(rows[0]["passes"], 1)
        self.assertAlmostEqual(rows[0]["avg_llm_score"], 0.9)

        if db_path.exists():
            db_path.unlink()


if __name__ == "__main__":
    unittest.main()
