from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestContentObservability(unittest.TestCase):
    def test_classify_markdown_emits_content_status_event(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        with patch(
            "kindly_web_search_mcp_server.content.status_classifier.emit_observability_event"
        ) as emit_event:
            result = classify_markdown("Access denied. Please verify you are human with captcha.")

        self.assertEqual(result.status, "blocked")
        self.assertEqual(emit_event.call_args.args[1], "content.status.classified")
        self.assertEqual(emit_event.call_args.kwargs["status"], "blocked")
        self.assertEqual(emit_event.call_args.kwargs["reason"], "access_blocked:access denied")
        self.assertFalse(emit_event.call_args.kwargs["cacheable"])

    def test_content_stage_events_persist_to_duckdb(self) -> None:
        from kindly_web_search_mcp_server.telemetry import (
            record_content_error,
            record_content_fallback,
            record_content_resolution,
        )

        with patch(
            "kindly_web_search_mcp_server.analytics.duckdb_store.append_event"
        ) as append_event:
            record_content_resolution(
                stage="safe_http",
                url="https://example.com",
                success=True,
                size_bytes=123,
                duration_seconds=0.5,
                word_count=20,
                extraction_method="trafilatura_safe",
            )
            record_content_fallback(
                stage="jina_reader",
                url="https://example.com",
                from_stage="safe_http",
            )
            record_content_error(
                stage="arxiv",
                url="https://example.com",
                error_type="arxiv_fetch_failed",
            )

        event_names = [call.args[0] for call in append_event.call_args_list]
        self.assertEqual(
            event_names,
            [
                "content.stage.resolution",
                "content.stage.fallback",
                "content.stage.error",
            ],
        )


if __name__ == "__main__":
    unittest.main()
