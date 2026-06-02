from __future__ import annotations

import time
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastmcp.exceptions import ToolError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestMiddlewareObservability(unittest.TestCase):
    def test_session_tracker_emits_session_events(self) -> None:
        from kindly_web_search_mcp_server.middleware.session_tracking import (
            SessionState,
            SessionTracker,
        )

        tracker = SessionTracker(timeout_seconds=1.0)
        with patch(
            "kindly_web_search_mcp_server.middleware.session_tracking.emit_observability_event"
        ) as emit_event:
            tracker.increment("session-1", "web_search")
            tracker.increment("session-1", "web_search")
            tracker._sessions["expired"] = SessionState(
                last_activity=time.time() - 2.0,
                counters={"web_search": 1},
            )
            tracker.cleanup_expired_sessions()

        event_names = [call.args[1] for call in emit_event.call_args_list]
        self.assertIn("session.started", event_names)
        self.assertIn("session.activity", event_names)
        self.assertIn("session.expired", event_names)

    def test_rate_limit_middleware_emits_acquired_and_throttled(self) -> None:
        from kindly_web_search_mcp_server.middleware.rate_limits import (
            DifferentiatedRateLimitMiddleware,
        )

        middleware = DifferentiatedRateLimitMiddleware(
            cheap_rps=1.0,
            cheap_burst=1,
            expensive_rps=1.0,
            expensive_burst=1,
        )
        middleware._cheap_limiter.acquire = AsyncMock(return_value=1.25)  # type: ignore[method-assign]
        context = SimpleNamespace(message=SimpleNamespace(name="web_search"))
        call_next = AsyncMock(return_value={"ok": True})

        with patch(
            "kindly_web_search_mcp_server.middleware.rate_limits.emit_observability_event"
        ) as emit_event:
            result = self._run_async(middleware.on_call_tool(context, call_next))

        self.assertEqual(result, {"ok": True})
        event_names = [call.args[1] for call in emit_event.call_args_list]
        self.assertIn("middleware.rate_limit.acquired", event_names)
        self.assertIn("middleware.rate_limit.throttled", event_names)

    def test_expensive_tool_middleware_emits_blocked_and_allowed(self) -> None:
        from kindly_web_search_mcp_server.middleware.expensive_tool_protection import (
            ExpensiveToolProtectionMiddleware,
        )

        middleware = ExpensiveToolProtectionMiddleware(block_first_attempt=True)
        context = SimpleNamespace(
            message=SimpleNamespace(name="perplexity_search"),
            fastmcp_context=None,
        )
        call_next = AsyncMock(return_value={"ok": True})

        with patch(
            "kindly_web_search_mcp_server.middleware.expensive_tool_protection.emit_observability_event"
        ) as emit_event:
            with self.assertRaises(ToolError):
                self._run_async(middleware.on_call_tool(context, call_next))
            result = self._run_async(middleware.on_call_tool(context, call_next))

        self.assertEqual(result, {"ok": True})
        event_names = [call.args[1] for call in emit_event.call_args_list]
        self.assertIn("middleware.expensive_tool.blocked", event_names)
        self.assertIn("middleware.expensive_tool.allowed", event_names)

    def test_format_tool_error_emits_error_classification(self) -> None:
        from kindly_web_search_mcp_server.errors import format_tool_error

        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(429, request=request, headers={"Retry-After": "12"})
        error = httpx.HTTPStatusError("rate limited", request=request, response=response)

        with patch(
            "kindly_web_search_mcp_server.errors.emit_observability_event"
        ) as emit_event:
            structured = format_tool_error(error, provider="searxng")

        self.assertTrue(structured["isError"])
        self.assertEqual(structured["error_type"], "rate_limit")
        self.assertEqual(emit_event.call_args.args[1], "tool.error.classified")
        self.assertEqual(emit_event.call_args.kwargs["provider"], "searxng")
        self.assertEqual(emit_event.call_args.kwargs["error_type"], "rate_limit")

    @staticmethod
    def _run_async(coro):
        import asyncio

        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
