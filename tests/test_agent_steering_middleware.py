from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from kindly_web_search_mcp_server.middleware.session_tracking import (
    SessionTracker,
    get_session_id,
)
from kindly_web_search_mcp_server.middleware.query_guidance import (
    DynamicGuidanceMiddleware,
)


class TestAgentSteeringMiddleware(unittest.IsolatedAsyncioTestCase):
    async def test_dynamic_guidance_on_web_search_empty(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(
                structured_content={
                    "query": "xyzzy",
                    "results": [],
                    "providers_used": ["searxng"],
                    "total_results": 0,
                }
            )

        context = MiddlewareContext(message=SimpleNamespace(name="web_search"))
        with patch(
            "kindly_web_search_mcp_server.middleware.query_guidance._gemini_is_available",
            return_value=True,
        ):
            result = await DynamicGuidanceMiddleware().on_call_tool(context, call_next)

        structured = result.structured_content
        guidance = structured["agent_guidance"][0]
        self.assertIn("Zero results", guidance["message"])
        self.assertIn("gemini_search", structured["suggested_next_tools"])

    async def test_dynamic_guidance_empty_coding_intent(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(
                structured_content={
                    "query": "asyncio.gather timeout",
                    "results": [],
                    "providers_used": ["github", "sourcegraph"],
                    "total_results": 0,
                    "intent": "ai_coding_and_infrastructure",
                }
            )

        context = MiddlewareContext(message=SimpleNamespace(name="web_search"))
        with patch(
            "kindly_web_search_mcp_server.middleware.query_guidance._gemini_is_available",
            return_value=True,
        ):
            result = await DynamicGuidanceMiddleware().on_call_tool(context, call_next)

        structured = result.structured_content
        guidance = structured["agent_guidance"][0]
        msg = guidance["message"].casefold()
        self.assertIn("zero results", msg)
        # Cause-aware: specialized/code path, not broaden-only
        self.assertTrue(
            "specialized" in msg or "code" in msg or "repo" in msg or "symbol" in msg,
            msg,
        )
        self.assertNotEqual(
            guidance["message"],
            "Zero results. Broaden: remove specific terms, set rewrite=true.",
        )

    async def test_dynamic_guidance_includes_query_shaping(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(
                structured_content={
                    "query": "python asyncio",
                    "results": [
                        {
                            "title": "docs",
                            "link": "https://docs.python.org/3/library/asyncio.html",
                            "provider_count": 2,
                        }
                    ],
                    "providers_used": ["searxng", "brave"],
                    "total_results": 1,
                    "intent": "ai_coding_and_infrastructure",
                    "query_shaping": [
                        {
                            "provider": "github",
                            "shaped": "python asyncio language:Python",
                            "rules": ["github.language"],
                        }
                    ],
                }
            )

        context = MiddlewareContext(message=SimpleNamespace(name="web_search"))
        with patch(
            "kindly_web_search_mcp_server.middleware.query_guidance._gemini_is_available",
            return_value=False,
        ):
            result = await DynamicGuidanceMiddleware().on_call_tool(context, call_next)

        structured = result.structured_content
        guidance = structured["agent_guidance"][0]
        self.assertIn("github", guidance["message"])
        self.assertIn("Query shaped", guidance["message"])

    async def test_dynamic_guidance_on_get_content_truncated(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(
                structured_content={
                    "input_url": "https://example.com",
                    "status": "success",
                    "source_type": "html",
                    "fetch_backend": "safe_http_extract",
                    "page_content": "x" * 500,
                    "window": {"has_more": True, "next_offset": 8000},
                }
            )

        context = MiddlewareContext(message=SimpleNamespace(name="get_content"))
        result = await DynamicGuidanceMiddleware().on_call_tool(context, call_next)

        structured = result.structured_content
        guidance = structured["agent_guidance"][0]
        self.assertIn("Truncated", guidance["message"])
        self.assertIn("char_offset=8000", guidance["message"])
        self.assertIn("get_content", structured["suggested_next_tools"])

    async def test_dynamic_guidance_skips_unregistered_tools(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(structured_content={"answer": "ok"})

        context = MiddlewareContext(message=SimpleNamespace(name="unknown_tool"))
        result = await DynamicGuidanceMiddleware().on_call_tool(context, call_next)

        # Should NOT have guidance for unregistered tools
        structured = result.structured_content
        self.assertNotIn("agent_guidance", structured)

    async def test_gemini_advisory_is_visible_on_first_calls(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(structured_content={"query": "fastmcp", "answer": "ok"})

        middleware = DynamicGuidanceMiddleware()
        context = MiddlewareContext(message=SimpleNamespace(name="gemini_search"))
        first = await middleware.on_call_tool(context, call_next)
        second = await middleware.on_call_tool(context, call_next)
        third = await middleware.on_call_tool(context, call_next)

        first_guidance = first.structured_content["agent_guidance"][0]
        second_guidance = second.structured_content["agent_guidance"][0]
        self.assertEqual(first_guidance["source"], "gemini_advisory")
        self.assertEqual(second_guidance["source"], "gemini_advisory")
        self.assertIn("quick grounded synthesis", first_guidance["message"])
        self.assertNotIn("agent_guidance", third.structured_content)

    def test_expensive_tool_session_id_does_not_use_global_default_session(self) -> None:
        context = MiddlewareContext(message=SimpleNamespace(name="grok_search"))

        session_id = get_session_id(context)

        self.assertNotEqual(session_id, "default_session")
        self.assertTrue(session_id.startswith("local_context:"))

    def test_session_tracker_expires_stale_sessions(self) -> None:
        tracker = SessionTracker(timeout_seconds=1.0)
        session_id = "session-1"

        self.assertEqual(tracker.increment(session_id, "gemini_search"), 1)
        self.assertEqual(tracker.get_count(session_id, "gemini_search"), 1)
        self.assertEqual(tracker.cleanup_expired_sessions(now=time.time() + 2.0), 1)
        self.assertEqual(tracker.get_count(session_id, "gemini_search"), 0)


if __name__ == "__main__":
    unittest.main()
