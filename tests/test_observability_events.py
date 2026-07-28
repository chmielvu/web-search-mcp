from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestObservabilityEventPrefixes(unittest.TestCase):
    def test_persisted_event_prefixes_include_all_analytics_families(self) -> None:
        from kindly_web_search_mcp_server.observability.events import (
            PERSISTED_EVENT_PREFIXES,
        )

        self.assertEqual(
            PERSISTED_EVENT_PREFIXES,
            (
                "query.rewrite.",
                "search.",
                "provider.",
                "tool.",
                "content.",
                "middleware.",
                "session.",
                "rerank.",
                "entity.",
                "eval.",
            ),
        )

    def test_non_tool_events_are_log_only_without_legacy_append_event(self) -> None:
        from kindly_web_search_mcp_server.utils import observability as obs

        logger = logging.getLogger(self._testMethodName)
        logger.addHandler(logging.NullHandler())
        expected_events = [
            "query.rewrite.completed",
            "search.merge.completed",
            "provider.search.result",
            "content.stage.resolution",
            "middleware.rate_limit.acquired",
            "session.started",
            "rerank.provider.completed",
            "entity.extraction.completed",
            "eval.case.completed",
        ]

        with (
            patch.object(
                obs, "_persist_analytics_event", wraps=obs._persist_analytics_event
            ) as persist,
            patch.object(logger, "debug") as debug_log,
            patch.object(logger, "log"),
        ):
            for event in expected_events:
                obs.emit_observability_event(logger, event, probe=True)
            obs.emit_observability_event(logger, "telemetry.startup", probe=True)

        persisted_events = [call.args[0] for call in persist.call_args_list]
        self.assertEqual(persisted_events, expected_events + ["telemetry.startup"])

        debug_events = [
            call.args[1]
            for call in debug_log.call_args_list
            if len(call.args) >= 2 and isinstance(call.args[1], str)
        ]
        for event in expected_events:
            self.assertIn(event, debug_events)
        self.assertNotIn("telemetry.startup", debug_events)

        import kindly_web_search_mcp_server.analytics.duckdb_store as duckdb_store

        self.assertFalse(hasattr(duckdb_store, "append_event"))

    def test_tool_events_still_use_typed_tool_calls_writer(self) -> None:
        from kindly_web_search_mcp_server.settings import settings
        from kindly_web_search_mcp_server.utils.observability import (
            emit_tool_observability_event,
        )

        logger = logging.getLogger(f"{self._testMethodName}.tool")
        logger.addHandler(logging.NullHandler())

        with (
            patch.object(settings, "analytics_enabled", True),
            patch(
                "kindly_web_search_mcp_server.analytics.duckdb_store.insert_tool_call_event"
            ) as insert_tool_call_event,
        ):
            emit_tool_observability_event(
                logger,
                "get_content",
                "response",
                url="https://example.com",
                status="success",
                result_count=1,
            )

        insert_tool_call_event.assert_called_once()
        kwargs = insert_tool_call_event.call_args.kwargs
        self.assertEqual(kwargs["tool_name"], "get_content")
        self.assertEqual(kwargs["phase"], "response")
        self.assertEqual(kwargs["status"], "success")


if __name__ == "__main__":
    unittest.main()
