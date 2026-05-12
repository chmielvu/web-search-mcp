"""FastMCP middleware for query quality and result extraction guidance.

Two non-blocking middleware components:
1. QueryQualityMiddleware - tips on every tool invocation
2. ResultGuidanceMiddleware - extraction guidance on tool results
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

logger = logging.getLogger(__name__)

# Concise query quality tips (shown on every invocation)
QUERY_QUALITY_TIPS = """
QUICK QUERY CHECK:
1. Keep rewrite=true for normal web discovery.
2. Use rewrite=false for exact errors, URLs, versions, hashes, UUIDs, or quoted strings.
3. Prefer one topic per call with domain, timeframe, and intended use.
"""

# Result extraction guidance (shown with results)
RESULT_EXTRACTION_GUIDANCE = """
RESULTS: Use provider_count as an agreement signal. Read selected URLs with get_content, or use batch_get_content for 3+ URLs. State gaps explicitly when results do not answer the goal.
"""


def _append_agent_guidance(result: Any, source: str, message: str) -> Any:
    """Attach visible guidance to structured MCP tool results."""
    if not isinstance(result, ToolResult) or not isinstance(result.structured_content, dict):
        return result

    structured = dict(result.structured_content)
    guidance = list(structured.get("agent_guidance") or [])
    guidance.append({"source": source, "message": message.strip()})
    structured["agent_guidance"] = guidance
    return ToolResult(structured_content=structured, meta=result.meta)


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

        # Keep a server-side breadcrumb for operators, but return the actual
        # steering in the MCP result so the calling agent can see it.
        logger.info(
            f"[QUERY TIPS] {tool_name}\n{QUERY_QUALITY_TIPS.strip()}"
        )

        result = await call_next(context)
        return _append_agent_guidance(result, "query_quality", QUERY_QUALITY_TIPS)


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

        return _append_agent_guidance(result, "result_guidance", RESULT_EXTRACTION_GUIDANCE)


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
