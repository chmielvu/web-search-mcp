from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any, cast
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
        self.assertEqual(prefix, '"md_my_db"."web_search_analytics".')
        connect.assert_called_once()
        self.assertNotIn("missing-local.duckdb", repr(connect.call_args))
        connection.execute.assert_called_once_with("ATTACH 'md:my_db' AS \"md_my_db\"")

    def test_build_analytics_query_plan_routes_provider_questions(self) -> None:
        from kindly_web_search_mcp_server.analytics.queries import (
            build_analytics_query_plan,
        )

        plan = build_analytics_query_plan(
            "provider performance for brave",
            view_prefix="main.",
            max_rows=12,
        )

        self.assertEqual(plan.rationale, "provider")
        self.assertIn("provider_calls", plan.sql)
        self.assertIn("LIMIT 12", plan.sql)

    def test_build_analytics_query_plan_rejects_removed_fetch_topic(self) -> None:
        from kindly_web_search_mcp_server.analytics.queries import (
            build_analytics_query_plan,
        )

        with self.assertRaises(ValueError) as ctx:
            build_analytics_query_plan("fetch quality for unified fetch windows")

        self.assertIn("Supported topics", str(ctx.exception))

    def test_run_analytics_query_returns_provider_metrics(self) -> None:
        from kindly_web_search_mcp_server.analytics import queries

        rows = [
            {
                "provider": "brave",
                "total_calls": 2,
                "success_count": 2,
                "success_rate_pct": 100.0,
                "avg_latency_ms": 10.0,
                "p95_latency_ms": 12.0,
                "total_results_returned": 5,
                "error_count": 0,
                "most_common_error": None,
            }
        ]
        local_result: dict[str, Any] = {
            "question": "provider performance",
            "scope": "local",
            "view_prefix": "main.",
            "rationale": "provider",
            "sql": "SELECT 1",
            "row_count": 1,
            "rows": rows,
        }

        with patch.object(queries, "run_local_analytics_query", return_value=local_result):
            result = cast(
                dict[str, Any],
                queries.run_analytics_query(
                    "provider performance",
                    scope="local",
                    max_rows=5,
                    db_path="analytics.duckdb",
                ),
            )

        self.assertEqual(result["rationale"], "provider")
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["rows"][0]["provider"], "brave")

    def test_analytics_query_cli_prints_json(self) -> None:
        from kindly_web_search_mcp_server import cli

        payload_data: dict[str, Any] = {
            "question": "provider performance",
            "scope": "local",
            "view_prefix": "main.",
            "rationale": "provider",
            "sql": "SELECT 1",
            "row_count": 1,
            "rows": [{"provider": "brave", "total_calls": 2}],
        }

        stdout = io.StringIO()
        with (
            patch("sys.stdout", new=stdout),
            patch(
                "kindly_web_search_mcp_server.analytics.queries.run_analytics_query",
                return_value=payload_data,
            ),
        ):
            cli.main(
                [
                    "analytics",
                    "query",
                    "--question",
                    "provider performance",
                    "--db-path",
                    "analytics.duckdb",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["data"]["rationale"], "provider")
        self.assertEqual(payload["data"]["row_count"], 1)
        self.assertEqual(payload["data"]["rows"][0]["provider"], "brave")


if __name__ == "__main__":
    unittest.main()
