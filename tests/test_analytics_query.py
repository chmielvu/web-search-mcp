from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestAnalyticsQuery(unittest.TestCase):
    def test_motherduck_connection_does_not_open_local_duckdb_path(self) -> None:
        from kindly_web_search_mcp_server.analytics import queries

        connection = Mock()

        with (
            patch.object(queries, "_motherduck_database", return_value="my_db"),
            patch.object(queries, "_load_motherduck"),
            patch.object(queries.duckdb, "connect", return_value=connection) as connect,
        ):
            returned_connection, prefix = queries._analytics_connection_and_prefix(
                Path("missing-local.duckdb"),
                scope="motherduck",
            )

        self.assertIs(returned_connection, connection)
        self.assertEqual(prefix, '"md_my_db"."kindly_analytics".')
        connect.assert_called_once()
        self.assertNotIn("missing-local.duckdb", repr(connect.call_args))
        connection.execute.assert_called_once_with('ATTACH \'md:my_db\' AS "md_my_db"')

    def test_build_analytics_query_plan_routes_fetch_questions(self) -> None:
        from kindly_web_search_mcp_server.analytics.queries import (
            build_analytics_query_plan,
        )

        plan = build_analytics_query_plan(
            "fetch quality for get_content windows",
            view_prefix="main.",
            max_rows=12,
        )

        self.assertEqual(plan.rationale, "fetch")
        self.assertIn("vw_fetch_events", plan.sql)
        self.assertIn("LIMIT 12", plan.sql)

    def test_run_analytics_query_returns_fetch_metrics(self) -> None:
        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event
        from kindly_web_search_mcp_server.analytics.queries import (
            run_analytics_query,
        )

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        append_event(
            "tool.get_content.response",
            {
                "input_url": "https://example.com/article",
                "normalized_url": "https://example.com/article",
                "fetched_url": "https://example.com/article",
                "status": "success",
                "source_type": "html",
                "fetch_backend": "safe_http_extract",
                "content_type": "text/markdown",
                "page_content": "alpha beta gamma",
                "word_count": 3,
                "window": {
                    "offset": 0,
                    "length": 20,
                    "returned_chars": 16,
                    "total_chars": 16,
                    "has_more": False,
                    "next_offset": None,
                },
                "metadata": {"title": "Example"},
                "links": [],
                "summary": None,
            },
            db_path=str(db_path),
        )

        result = run_analytics_query(
            "fetch quality",
            scope="local",
            max_rows=5,
            db_path=str(db_path),
        )

        self.assertEqual(result["rationale"], "fetch")
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["view_prefix"], "main.")
        row = result["rows"][0]
        self.assertEqual(row["fetch_backend"], "safe_http_extract")
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["avg_word_count"], 3.0)
        self.assertEqual(row["partial_windows"], 0)

        if db_path.exists():
            db_path.unlink()

    def test_analytics_query_cli_prints_json(self) -> None:
        from kindly_web_search_mcp_server import cli
        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        append_event(
            "tool.get_content.response",
            {
                "input_url": "https://example.com/article",
                "normalized_url": "https://example.com/article",
                "fetched_url": "https://example.com/article",
                "status": "success",
                "source_type": "html",
                "fetch_backend": "safe_http_extract",
                "content_type": "text/markdown",
                "page_content": "alpha beta gamma",
                "word_count": 3,
                "window": {
                    "offset": 0,
                    "length": 20,
                    "returned_chars": 16,
                    "total_chars": 16,
                    "has_more": False,
                    "next_offset": None,
                },
            },
            db_path=str(db_path),
        )

        stdout = io.StringIO()
        with patch("sys.stdout", new=stdout):
            cli.main(
                [
                    "analytics-query",
                    "--question",
                    "fetch quality",
                    "--duckdb-path",
                    str(db_path),
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["rationale"], "fetch")
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["fetch_backend"], "safe_http_extract")

        if db_path.exists():
            db_path.unlink()


if __name__ == "__main__":
    unittest.main()
