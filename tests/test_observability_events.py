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

    def test_shared_prefixes_control_duckdb_persistence_whitelist(self) -> None:
        from kindly_web_search_mcp_server.utils.observability import (
            emit_observability_event,
        )

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

        with patch(
            "kindly_web_search_mcp_server.analytics.duckdb_store.append_event"
        ) as append_event:
            for event in expected_events:
                emit_observability_event(logger, event, probe=True)
            emit_observability_event(logger, "telemetry.startup", probe=True)

        self.assertEqual(
            [call.args[0] for call in append_event.call_args_list],
            expected_events,
        )


if __name__ == "__main__":
    unittest.main()
