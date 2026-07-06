from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
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

    def test_append_event_normalizes_provider_name_and_count_aliases(self) -> None:
        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event

        import duckdb

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        append_event(
            "provider.search.result",
            {
                "provider_name": "searxng",
                "query": "duckdb json_each",
                "num_results_requested": 9,
                "result_count": 7,
                "results": [],
            },
            db_path=str(db_path),
        )

        con = duckdb.connect(str(db_path), read_only=True)
        row = con.execute(
            "SELECT provider, input_count, output_count FROM search_events"
        ).fetchone()
        con.close()

        self.assertEqual(row, ("searxng", 9, 7))

        if db_path.exists():
            db_path.unlink()


class TestDuckDBAnalyticsAsync(unittest.IsolatedAsyncioTestCase):
    async def test_append_event_returns_before_write_finishes(self) -> None:
        from kindly_web_search_mcp_server.analytics import duckdb_store

        write_started = threading.Event()

        class SlowConnection:
            def execute(self, *args, **kwargs):
                write_started.set()
                time.sleep(0.15)
                return self

            def close(self) -> None:
                return None

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        with (
            patch.object(duckdb_store.settings, "analytics_enabled", True),
            patch.object(duckdb_store, "_ensure_schema", return_value=None),
            patch.object(
                duckdb_store.duckdb,
                "connect",
                return_value=SlowConnection(),
            ),
        ):
            start = time.perf_counter()
            duckdb_store.append_event(
                "provider.search.result",
                {"query": "FastMCP", "provider": "searxng"},
                db_path=str(db_path),
            )
            elapsed = time.perf_counter() - start

            self.assertLess(elapsed, 0.05)
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(write_started.wait), 1.0))
            await asyncio.sleep(0.2)

    async def test_insert_provider_calls_returns_before_write_finishes(self) -> None:
        from kindly_web_search_mcp_server.analytics import duckdb_store

        write_started = threading.Event()

        class SlowConnection:
            def execute(self, *args, **kwargs):
                write_started.set()
                time.sleep(0.15)
                return self

            def close(self) -> None:
                return None

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        with (
            patch.object(duckdb_store.settings, "analytics_enabled", True),
            patch.object(duckdb_store, "_ensure_provider_calls", return_value=None),
            patch.object(
                duckdb_store.duckdb,
                "connect",
                return_value=SlowConnection(),
            ),
        ):
            start = time.perf_counter()
            duckdb_store.insert_provider_calls(
                run_key="run-1",
                provider="searxng",
                branch_index=0,
                branch_query="FastMCP",
                num_results_requested=10,
                num_results_returned=8,
                duration_ms=123.4,
                error_code=None,
                error_message=None,
                http_status=200,
                payload_json=None,
                db_path=str(db_path),
            )
            elapsed = time.perf_counter() - start

            self.assertLess(elapsed, 0.05)
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(write_started.wait), 1.0))
            await asyncio.sleep(0.2)

    async def test_provider_health_transition_returns_before_write_finishes(self) -> None:
        from kindly_web_search_mcp_server.analytics import observability_inserts

        write_started = threading.Event()

        class SlowConnection:
            def execute(self, *args, **kwargs):
                write_started.set()
                time.sleep(0.15)
                return self

            def close(self) -> None:
                return None

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        with (
            patch.object(observability_inserts.settings, "analytics_enabled", True),
            patch.object(
                observability_inserts,
                "ensure_pipeline_observability_tables",
                return_value=None,
            ),
            patch.object(
                observability_inserts.duckdb,
                "connect",
                return_value=SlowConnection(),
            ),
        ):
            start = time.perf_counter()
            observability_inserts.insert_provider_health_transition(
                provider="searxng",
                transition="cooldown",
                run_key="run-2",
                tool_call_id="tool-1",
                status="closed",
                consecutive_failures=1,
                cooldown_seconds=1.0,
                cooldown_remaining_s=1.0,
                total_successes=0,
                total_failures=1,
                error_type="TimeoutError",
                is_rate_limit=False,
                circuit_state="closed",
                payload_json=None,
                db_path=str(db_path),
            )
            elapsed = time.perf_counter() - start

            self.assertLess(elapsed, 0.05)
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(write_started.wait), 1.0))
            await asyncio.sleep(0.2)

    def test_append_event_normalizes_model_used_and_token_aliases(self) -> None:
        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event

        import duckdb

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        append_event(
            "tool.gemini_search.response",
            {
                "tool_name": "gemini_search",
                "query": "FastMCP docs",
                "model_used": "gemini-2.5-flash",
                "input_tokens": 37,
                "output_tokens": 19,
            },
            db_path=str(db_path),
        )

        con = duckdb.connect(str(db_path), read_only=True)
        row = con.execute(
            "SELECT model, model_used, input_tokens, output_tokens FROM search_events"
        ).fetchone()
        con.close()

        self.assertEqual(row, ("gemini-2.5-flash", "gemini-2.5-flash", 37, 19))

        if db_path.exists():
            db_path.unlink()

    def test_append_event_normalizes_agentic_completion_shape(self) -> None:
        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event

        import duckdb

        db_path = Path(self._testMethodName).with_suffix(".duckdb")
        if db_path.exists():
            db_path.unlink()

        append_event(
            "agentic.research.completed",
            {
                "tool_name": "agentic_web_research",
                "query": "How does LangGraph ReAct work?",
                "research_goal": "shape smoke test",
                "model": "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B",
                "duration_seconds": 1.234,
                "tool_calls_count": 2,
                "sources_count": 8,
                "trace_id": "trace-agentic-1",
                "span_id": "span-agentic-1",
            },
            db_path=str(db_path),
        )

        con = duckdb.connect(str(db_path), read_only=True)
        row = con.execute(
            """
            SELECT event_name, tool_name, duration_ms, input_count, output_count, phase
            FROM search_events
            """
        ).fetchone()
        con.close()

        self.assertEqual(
            row,
            (
                "agentic.research.completed",
                "agentic_web_research",
                1234.0,
                2,
                8,
                "completed",
            ),
        )

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

    def test_schema_migration_backfills_provider_and_count_aliases(self) -> None:
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
                'provider.search.result',
                CURRENT_TIMESTAMP,
                'query',
                'query',
                'goal',
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                '{"provider_name":"brave","num_results_requested":5,"result_count":3}'
            )
            """
        )
        con.close()

        ensure_store_schema(db_path=str(db_path))

        con = duckdb.connect(str(db_path), read_only=True)
        row = con.execute(
            "SELECT provider, input_count, output_count FROM search_events"
        ).fetchone()
        con.close()

        self.assertEqual(row, ("brave", 5, 3))

        if db_path.exists():
            db_path.unlink()

    def test_motherduck_sql_uses_views_and_summary_tables(self) -> None:
        from kindly_web_search_mcp_server.analytics.motherduck_sync import (
            build_analytics_view_sql,
            build_eval_table_sql,
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
        self.assertIn("vw_events", view_sql)
        self.assertIn("$.provider_name", view_sql)
        self.assertIn("vw_provider_results", view_sql)
        self.assertIn("vw_branch_candidates", view_sql)
        self.assertIn("vw_merged_results", view_sql)
        self.assertIn("vw_search_results", view_sql)
        self.assertIn("vw_rerank_results", view_sql)
        self.assertIn("vw_rewrite_variants", view_sql)
        self.assertIn("vw_fetch_events", view_sql)
        self.assertIn("vw_answer_events", view_sql)
        self.assertIn("vw_candidate_survival", view_sql)
        self.assertIn("vw_eval_provider_quality", view_sql)
        self.assertIn("vw_eval_fetch_quality", view_sql)
        self.assertIn("eval_quality_daily", view_sql)
        self.assertIn("create table if not exists", table_sql)
        self.assertIn("analytics_sync_state", table_sql)
        self.assertNotIn("materialized view", view_sql)

    def test_quality_dashboard_includes_motherduck_survival_panels(self) -> None:
        dashboard_path = (
            Path(__file__).resolve().parents[1]
            / "grafana"
            / "dashboards"
            / "kindly-mcp-quality-dashboard.json"
        )
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

        templating_names = {variable["name"] for variable in dashboard["templating"]["list"]}
        panels_by_id = {panel["id"]: panel for panel in dashboard["panels"]}

        self.assertEqual(dashboard["version"], 3)
        self.assertIn("motherduck", dashboard["tags"])
        self.assertIn("motherduck_datasource", templating_names)
        datasource_variable = next(
            variable
            for variable in dashboard["templating"]["list"]
            if variable["name"] == "motherduck_datasource"
        )
        self.assertEqual(datasource_variable["pluginId"], "grafana-postgresql-datasource")
        self.assertEqual(panels_by_id[13]["datasource"]["type"], "grafana-postgresql-datasource")
        self.assertIn("vw_candidate_survival", panels_by_id[13]["targets"][0]["rawSql"])
        self.assertEqual(panels_by_id[14]["datasource"]["type"], "grafana-postgresql-datasource")
        self.assertIn("provider.search.result", panels_by_id[14]["targets"][0]["rawSql"])
        self.assertEqual(panels_by_id[15]["datasource"]["type"], "grafana-postgresql-datasource")
        self.assertIn("vw_provider_results", panels_by_id[15]["targets"][0]["rawSql"])
        self.assertIn("source_engines_json", panels_by_id[15]["targets"][0]["rawSql"])


if __name__ == "__main__":
    unittest.main()
