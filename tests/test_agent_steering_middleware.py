from __future__ import annotations

import time
from types import SimpleNamespace
import unittest

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
    async def test_dynamic_guidance_on_web_search_with_results(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(structured_content={
                "query": "fastmcp",
                "results": [
                    {"title": "t1", "link": "https://github.com/org/repo/issues/1", "snippet": "...", "provider_count": 1},
                    {"title": "t2", "link": "https://github.com/org/repo/issues/2", "snippet": "...", "provider_count": 1},
                    {"title": "t3", "link": "https://github.com/org/repo/issues/3", "snippet": "...", "provider_count": 1},
                ],
                "providers_used": ["searxng", "ddg"],
                "total_results": 3,
            })

        context = MiddlewareContext(message=SimpleNamespace(name="web_search"))
        result = await DynamicGuidanceMiddleware().on_call_tool(context, call_next)

        structured = result.structured_content
        # Should have agent_guidance
        self.assertIn("agent_guidance", structured)
        guidance = structured["agent_guidance"][0]
        self.assertEqual(guidance["source"], "dynamic_guidance")
        # Should mention github.com specialized resolver and composio_similarlinks
        self.assertIn("github.com", guidance["message"])
        # Should have suggested_next_tools
        self.assertIn("suggested_next_tools", structured)
        self.assertIn("composio_similarlinks", structured["suggested_next_tools"])
        # Should have suggested_prompts
        self.assertIn("suggested_prompts", structured)
        self.assertIn("evaluate_web_results", structured["suggested_prompts"])

    async def test_dynamic_guidance_on_web_search_empty(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(structured_content={
                "query": "xyzzy",
                "results": [],
                "providers_used": ["searxng"],
                "total_results": 0,
            })

        context = MiddlewareContext(message=SimpleNamespace(name="web_search"))
        result = await DynamicGuidanceMiddleware().on_call_tool(context, call_next)

        structured = result.structured_content
        guidance = structured["agent_guidance"][0]
        self.assertIn("Zero results", guidance["message"])
        self.assertIn("gemini_search", structured["suggested_next_tools"])

    async def test_dynamic_guidance_on_get_content_truncated(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(structured_content={
                "input_url": "https://example.com",
                "status": "success",
                "source_type": "html",
                "fetch_backend": "safe_http_extract",
                "page_content": "x" * 500,
                "window": {"has_more": True, "next_offset": 8000},
            })

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
        context = MiddlewareContext(message=SimpleNamespace(name="perplexity_search"))

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
