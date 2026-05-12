from __future__ import annotations

from types import SimpleNamespace
import unittest

from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from kindly_web_search_mcp_server.middleware.expensive_tool_protection import (
    ExpensiveToolProtectionMiddleware,
)
from kindly_web_search_mcp_server.middleware.gemini_advisory import GeminiAdvisoryMiddleware
from kindly_web_search_mcp_server.middleware.query_guidance import (
    QueryQualityMiddleware,
    ResultGuidanceMiddleware,
)


class TestAgentSteeringMiddleware(unittest.IsolatedAsyncioTestCase):
    async def test_query_and_result_guidance_are_visible_in_structured_result(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(structured_content={"query": "fastmcp", "results": []})

        context = MiddlewareContext(message=SimpleNamespace(name="web_search"))
        query_guided = await QueryQualityMiddleware().on_call_tool(context, call_next)
        result_guided = await ResultGuidanceMiddleware().on_call_tool(context, call_next)

        self.assertEqual(
            query_guided.structured_content["agent_guidance"][0]["source"],
            "query_quality",
        )
        self.assertIn(
            "rewrite=true",
            query_guided.structured_content["agent_guidance"][0]["message"],
        )
        self.assertEqual(
            result_guided.structured_content["agent_guidance"][0]["source"],
            "result_guidance",
        )
        self.assertIn(
            "batch_get_content",
            result_guided.structured_content["agent_guidance"][0]["message"],
        )

    async def test_gemini_advisory_is_visible_on_first_calls(self) -> None:
        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(structured_content={"query": "fastmcp", "answer": "ok"})

        context = MiddlewareContext(message=SimpleNamespace(name="gemini_search"))
        result = await GeminiAdvisoryMiddleware().on_call_tool(context, call_next)

        guidance = result.structured_content["agent_guidance"][0]
        self.assertEqual(guidance["source"], "gemini_advisory")
        self.assertIn("quick grounded synthesis", guidance["message"])

    def test_expensive_tool_session_id_does_not_use_global_default_session(self) -> None:
        middleware = ExpensiveToolProtectionMiddleware()
        context = MiddlewareContext(message=SimpleNamespace(name="perplexity_search"))

        session_id = middleware._get_session_id(context)

        self.assertNotEqual(session_id, "default_session")
        self.assertTrue(session_id.startswith("local_context:"))


if __name__ == "__main__":
    unittest.main()
