from __future__ import annotations

import json
import logging
import sys
import unittest
from unittest.mock import patch
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
            SELECT
                event_name,
                query,
                research_goal,
                provider,
                model,
                run_key,
                phase,
                payload_json
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
        self.assertEqual(row[5], "trace-123")
        self.assertEqual(row[6], "completed")
        self.assertEqual(json.loads(row[7])["span_id"], "span-456")

        if db_path.exists():
            db_path.unlink()

    def test_tool_events_persist_full_text_payload(self) -> None:
        from kindly_web_search_mcp_server.utils.observability import (
            emit_tool_observability_event,
        )

        logger = logging.getLogger(self._testMethodName)
        logger.addHandler(logging.NullHandler())
        full_text = "x" * 2500

        with patch(
            "kindly_web_search_mcp_server.analytics.duckdb_store.append_event"
        ) as append_event:
            emit_tool_observability_event(
                logger,
                "get_content",
                "response",
                input_url="https://example.com/a",
                normalized_url="https://example.com/a",
                fetched_url="https://example.com/a",
                page_content=full_text,
                metadata={"title": "Example"},
            )

        append_event.assert_called_once()
        event_name, payload = append_event.call_args.args
        self.assertEqual(event_name, "tool.get_content.response")
        self.assertEqual(payload["page_content"], full_text)
        self.assertEqual(payload["fetched_url"], "https://example.com/a")

    def test_schema_migration_backfills_event_ids(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            ensure_store_schema,
        )

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        con = duckdb.connect(str(db_path))
        con.execute(
            """
            CREATE TABLE search_events (
                event_name VARCHAR,
                recorded_at TIMESTAMP,
                query VARCHAR,
                normalized_query VARCHAR,
                research_goal VARCHAR,
                provider VARCHAR,
                model VARCHAR,
                duration_ms DOUBLE,
                input_count INTEGER,
                output_count INTEGER,
                trace_id VARCHAR,
                span_id VARCHAR,
                payload_json VARCHAR
            )
            """
        )
        con.execute(
            """
            INSERT INTO search_events
            VALUES (
                'tool.web_search.response',
                CURRENT_TIMESTAMP,
                'query',
                'query',
                'goal',
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                'trace-1',
                'span-1',
                '{"tool_name":"web_search"}'
            )
            """
        )
        con.close()

        ensure_store_schema(db_path=str(db_path))

        con = duckdb.connect(str(db_path), read_only=True)
        row = con.execute(
            "SELECT event_id, run_key, tool_name, phase FROM search_events"
        ).fetchone()
        con.close()

        assert row is not None
        self.assertIsInstance(row[0], str)
        self.assertEqual(row[1], "trace-1")
        self.assertEqual(row[2], "web_search")
        self.assertEqual(row[3], "response")

        if db_path.exists():
            db_path.unlink()

    def test_motherduck_sql_uses_views_and_summary_tables(self) -> None:
        from kindly_web_search_mcp_server.analytics.motherduck_sync import (
            build_analytics_view_sql,
            build_summary_sql,
        )

        sql = "\n".join(
            [
                *build_analytics_view_sql('"md"."kindly_analytics"'),
                *build_summary_sql('"md"."kindly_analytics"'),
            ]
        ).lower()

        self.assertIn("create or replace view", sql)
        self.assertIn("vw_provider_results", sql)
        self.assertIn("vw_branch_candidates", sql)
        self.assertIn("vw_merged_results", sql)
        self.assertIn("vw_search_results", sql)
        self.assertIn("vw_rerank_results", sql)
        self.assertIn("vw_rewrite_variants", sql)
        self.assertIn("vw_fetch_events", sql)
        self.assertIn("vw_answer_events", sql)
        self.assertIn("vw_candidate_survival", sql)
        self.assertIn("create or replace table", sql)
        self.assertNotIn("materialized view", sql)

    def test_quality_dashboard_includes_motherduck_survival_panels(self) -> None:
        dashboard_path = (
            Path(__file__).resolve().parents[1]
            / "grafana"
            / "dashboards"
            / "kindly-mcp-quality-dashboard.json"
        )
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

        templating_names = {
            variable["name"] for variable in dashboard["templating"]["list"]
        }
        panels_by_id = {panel["id"]: panel for panel in dashboard["panels"]}

        self.assertEqual(dashboard["version"], 2)
        self.assertIn("motherduck", dashboard["tags"])
        self.assertIn("motherduck_datasource", templating_names)
        self.assertEqual(
            panels_by_id[13]["datasource"]["type"], "motherduck-duckdb-datasource"
        )
        self.assertIn("vw_candidate_survival", panels_by_id[13]["targets"][0]["rawSql"])
        self.assertEqual(
            panels_by_id[14]["datasource"]["type"], "motherduck-duckdb-datasource"
        )
        self.assertIn(
            "provider.search.result", panels_by_id[14]["targets"][0]["rawSql"]
        )
        self.assertEqual(
            panels_by_id[15]["datasource"]["type"], "motherduck-duckdb-datasource"
        )
        self.assertIn("vw_provider_results", panels_by_id[15]["targets"][0]["rawSql"])
        self.assertIn("source_engines_json", panels_by_id[15]["targets"][0]["rawSql"])


if __name__ == "__main__":
    unittest.main()
