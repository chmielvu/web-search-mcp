"""FastMCP middleware for query quality and result extraction guidance.

Two non-blocking middleware components:
1. QueryQualityMiddleware - tips on every tool invocation
2. ResultGuidanceMiddleware - extraction guidance on tool results
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger(__name__)

# Concise query quality tips (shown on every invocation)
QUERY_QUALITY_TIPS = """
📋 QUICK QUERY CHECK:
1. Specific > vague ("React 19 hooks" not "React")
2. One topic per call — split multi-part questions
3. Use exact terms: error codes, API names, versions
4. Add context: domain + timeframe + need
5. Quote exact errors for debugging
"""

# Result extraction guidance (shown with results)
RESULT_EXTRACTION_GUIDANCE = """
📌 RESULTS: Selected sources above.
→ Extract key facts, compare across sources.
→ Need more? Refine query and call again.
→ Missing info? State gap explicitly.
"""


class QueryQualityMiddleware(Middleware):
    """Non-blocking middleware showing query tips on EVERY web_search call.

    Provides concise, actionable guidance without blocking execution.
    Tips appear in logs for every invocation.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: Any,
    ) -> Any:
        """Log query quality tips before web_search execution.

        Non-blocking: call proceeds immediately after logging tips.
        Only applies to web_search tool, shows tips EVERY call.
        """
        tool_name = context.message.name

        # Only show tips for web_search
        if tool_name != "web_search":
            return await call_next(context)

        # Show tips on EVERY call (non-blocking, informational)
        logger.info(
            f"[QUERY TIPS] {tool_name}\n{QUERY_QUALITY_TIPS.strip()}"
        )

        # Call proceeds normally
        return await call_next(context)


class ResultGuidanceMiddleware(Middleware):
    """Non-blocking middleware showing extraction guidance with EVERY web_search result.

    Appends actionable guidance to tool results for better follow-up.
    Only applies to web_search tool, shows guidance on EVERY call.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: Any,
    ) -> Any:
        """Add extraction guidance after web_search execution.

        Non-blocking: guidance logged on EVERY call.
        """
        tool_name = context.message.name

        # Only add guidance for web_search
        if tool_name != "web_search":
            return await call_next(context)

        # Execute tool
        result = await call_next(context)

        # Log guidance on EVERY call
        logger.info(
            f"[RESULT GUIDE] {tool_name} returned\n{RESULT_EXTRACTION_GUIDANCE.strip()}"
        )

        return result


def create_query_quality_middleware() -> QueryQualityMiddleware:
    """Factory for query quality middleware (web_search only, every call)."""
    return QueryQualityMiddleware()


def create_result_guidance_middleware() -> ResultGuidanceMiddleware:
    """Factory for result guidance middleware (web_search only, every call)."""
    return ResultGuidanceMiddleware()


__all__ = [
    "QUERY_QUALITY_TIPS",
    "RESULT_EXTRACTION_GUIDANCE",
    "QueryQualityMiddleware",
    "ResultGuidanceMiddleware",
    "create_query_quality_middleware",
    "create_result_guidance_middleware",
]