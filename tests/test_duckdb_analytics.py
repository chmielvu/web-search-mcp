from __future__ import annotations

import json
import logging
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestDuckDBAnalytics(unittest.TestCase):
    def test_tool_call_writer_persists_typed_lifecycle_rows(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.async_writes import drain_duckdb_writes
        from kindly_web_search_mcp_server.analytics.duckdb_store import insert_tool_call_event
        from kindly_web_search_mcp_server.analytics.writers.schema import ensure_store_schema

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        db_path.unlink(missing_ok=True)
        ensure_store_schema(db_path=str(db_path))

        insert_tool_call_event(
            tool_call_id="tool-1",
            tool_name="web_search",
            phase="request",
            status="started",
            query="FastMCP",
            request_fingerprint="fingerprint-1",
            payload_json={"query": "FastMCP", "span_id": "span-456"},
            db_path=str(db_path),
        )
        insert_tool_call_event(
            tool_call_id="tool-1",
            tool_name="web_search",
            phase="response",
            status="success",
            query="FastMCP",
            output_count=2,
            duration_ms=12.5,
            payload_json={"results": [{"title": "docs"}]},
            db_path=str(db_path),
        )
        drain_duckdb_writes()

        con = duckdb.connect(str(db_path), read_only=True)
        rows = con.execute(
            "SELECT tool_call_id, phase, status, output_count, payload_json FROM tool_calls ORDER BY recorded_at, phase"
        ).fetchall()
        con.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], rows[1][0])
        self.assertEqual(
            rows[0][1:], ("request", "started", None, '{"query": "FastMCP", "span_id": "span-456"}')
        )
        self.assertEqual(rows[1][1], "response")
        self.assertEqual(json.loads(rows[1][4])["results"][0]["title"], "docs")
        db_path.unlink(missing_ok=True)

    def test_provider_writer_persists_request_diagnostics(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.async_writes import drain_duckdb_writes
        from kindly_web_search_mcp_server.analytics.duckdb_store import insert_provider_calls

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        db_path.unlink(missing_ok=True)
        insert_provider_calls(
            run_key="run-1",
            branch_index=0,
            provider="sourcegraph",
            branch_query="literal query",
            status="success",
            num_results_requested=10,
            num_results_returned=2,
            request_query="literal query",
            request_url="https://sourcegraph.com/.api/graphql",
            http_status=200,
            result_class="nonempty",
            response_meta_json={"pattern_type": "literal", "match_count": 2},
            db_path=str(db_path),
        )
        drain_duckdb_writes()

        con = duckdb.connect(str(db_path), read_only=True)
        row = con.execute(
            "SELECT provider, request_query, request_url, http_status, result_class, response_meta_json FROM provider_calls"
        ).fetchone()
        con.close()

        self.assertEqual(
            row[:5],
            (
                "sourcegraph",
                "literal query",
                "https://sourcegraph.com/.api/graphql",
                200,
                "nonempty",
            ),
        )
        self.assertEqual(json.loads(row[5])["match_count"], 2)
        db_path.unlink(missing_ok=True)

    def test_tool_event_payload_is_bounded_and_excludes_credentials(self) -> None:
        from kindly_web_search_mcp_server.analytics.duckdb_store import insert_tool_call_event
        from kindly_web_search_mcp_server.utils.observability import emit_tool_observability_event
        from kindly_web_search_mcp_server.analytics.async_writes import drain_duckdb_writes
        from kindly_web_search_mcp_server.analytics.writers.schema import ensure_store_schema
        import duckdb

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        db_path.unlink(missing_ok=True)
        ensure_store_schema(db_path=str(db_path))
        logger = logging.getLogger(self._testMethodName)
        logger.addHandler(logging.NullHandler())
        with patch(
            "kindly_web_search_mcp_server.analytics.duckdb_store.insert_tool_call_event",
            lambda **kwargs: insert_tool_call_event(db_path=str(db_path), **kwargs),
        ):
            emit_tool_observability_event(
                logger,
                "fetch",
                "response",
                page_content="x" * 50000,
                authorization="Bearer do-not-store",
                input_url="https://example.com",
            )
        drain_duckdb_writes()
        con = duckdb.connect(str(db_path), read_only=True)
        payload = con.execute("SELECT payload_json FROM tool_calls").fetchone()[0]
        con.close()
        self.assertLess(len(payload), 25000)
        self.assertNotIn("do-not-store", payload)
        db_path.unlink(missing_ok=True)

    def test_schema_contains_typed_observability_tables(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.writers.schema import ensure_store_schema

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        db_path.unlink(missing_ok=True)
        ensure_store_schema(db_path=str(db_path))
        con = duckdb.connect(str(db_path), read_only=True)
        tables = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_name IN ('tool_calls', 'query_understanding_events')"
            ).fetchall()
        }
        con.close()
        self.assertEqual(tables, {"tool_calls", "query_understanding_events"})
        db_path.unlink(missing_ok=True)

    def test_motherduck_sql_uses_current_fact_views(self) -> None:
        from kindly_web_search_mcp_server.analytics.evals import build_eval_table_sql
        from kindly_web_search_mcp_server.analytics.motherduck_sync import (
            build_analytics_view_sql,
            build_summary_sql,
        )

        view_sql = "\n".join(
            [
                *build_analytics_view_sql('"md"."kindly_analytics"'),
                *build_summary_sql('"md"."kindly_analytics"'),
            ]
        ).lower()
        table_sql = "\n".join(build_eval_table_sql('"md"."kindly_analytics"')).lower()

        self.assertIn("create or replace view", view_sql)
        self.assertIn("vw_provider_performance", view_sql)
        self.assertIn("vw_query_understanding_calibration", view_sql)
        self.assertIn("create table if not exists", table_sql)
        self.assertIn("analytics_sync_state", table_sql)
        self.assertNotIn("search_events", view_sql)

    def test_quality_dashboard_includes_otel_quality_panels(self) -> None:
        dashboard_path = (
            Path(__file__).resolve().parents[1]
            / "grafana"
            / "dashboards"
            / "kindly-mcp-quality-dashboard.json"
        )
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

        templating_names = {variable["name"] for variable in dashboard["templating"]["list"]}
        panels_by_id = {panel["id"]: panel for panel in dashboard["panels"]}

        # v2 dashboard overhaul (2026-07): quality panels are Prometheus/OTel-fed.
        # No postgres/MotherDuck datasource remains in any dashboard.
        self.assertEqual(dashboard["version"], 1)
        self.assertIn("quality", dashboard["tags"])
        self.assertIn("datasource", templating_names)
        self.assertEqual(panels_by_id[1]["datasource"]["type"], "prometheus")
        self.assertEqual(panels_by_id[13]["datasource"]["type"], "prometheus")
        self.assertEqual(panels_by_id[14]["datasource"]["type"], "prometheus")


class TestDuckDBAnalyticsAsync(unittest.IsolatedAsyncioTestCase):
    async def test_tool_call_insert_dispatches_without_blocking(self) -> None:
        from kindly_web_search_mcp_server.analytics import duckdb_store
        from kindly_web_search_mcp_server.analytics.writers import inserts

        with patch.object(inserts, "_TOOL_CALLS_WRITER") as writer:
            start = time.perf_counter()
            duckdb_store.insert_tool_call_event(
                tool_call_id="tool-1",
                tool_name="web_search",
                phase="request",
                db_path="unused.duckdb",
            )
            elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.05)
        writer.dispatch_insert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
