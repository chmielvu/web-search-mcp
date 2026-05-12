"""FastMCP middleware for Gemini search advisory (non-blocking).

Provides informational guidance for query quality without blocking calls.
Gemini with Google Search grounding is cheap, so this is advisory-only.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

logger = logging.getLogger(__name__)

# Tools to provide advisory guidance for
GEMINI_TOOLS = frozenset({"gemini_search"})

# Advisory message for Gemini query best practices - informative, not blocking
GEMINI_QUERY_ADVISORY = """
GEMINI SEARCH: Best for quick grounded synthesis. Use a single focused question, include exact API/error/version terms, and add recency hints when freshness matters. Use web_search plus get_content when you need to compare source pages yourself.
"""


def _append_agent_guidance(result: Any, message: str) -> Any:
    """Attach Gemini advisory to structured MCP tool results."""
    if not isinstance(result, ToolResult) or not isinstance(result.structured_content, dict):
        return result

    structured = dict(result.structured_content)
    guidance = list(structured.get("agent_guidance") or [])
    guidance.append({"source": "gemini_advisory", "message": message.strip()})
    structured["agent_guidance"] = guidance
    return ToolResult(structured_content=structured, meta=result.meta)


class GeminiAdvisoryMiddleware(Middleware):
    """Middleware that provides advisory guidance for Gemini search calls.

    Non-blocking: the call proceeds, but context includes query tips.
    This helps improve query quality over time without forcing retries.
    """

    def __init__(
        self,
        advisory_tools: frozenset[str] = GEMINI_TOOLS,
    ):
        """Initialize advisory middleware.

        Args:
            advisory_tools: Set of tool names to provide guidance for
        """
        self.advisory_tools = advisory_tools
        self._call_counts: dict[str, int] = {}  # session -> count (for logging)

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: Any,
    ) -> Any:
        """Add advisory message to Gemini calls (non-blocking).

        Args:
            context: MiddlewareContext with tool call details
            call_next: Next handler in chain

        Returns:
            Tool result (call proceeds normally)
        """
        tool_name = context.message.name

        # Only advise for Gemini tools
        if tool_name not in self.advisory_tools:
            return await call_next(context)

        session_id = self._get_session_id(context)
        call_count = self._call_counts.get(session_id, 0) + 1
        self._call_counts[session_id] = call_count

        if call_count <= 2:  # Only log advisory for first couple calls
            logger.info(
                f"Gemini advisory for {tool_name} "
                f"(session={session_id}, call={call_count}) - "
                f"query tips available but call proceeds"
            )

        result = await call_next(context)
        if call_count <= 2:
            return _append_agent_guidance(result, GEMINI_QUERY_ADVISORY)
        return result

    def _get_session_id(self, context: MiddlewareContext) -> str:
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is not None:
            try:
                return fastmcp_context.session_id
            except RuntimeError:
                client_id = fastmcp_context.client_id
                if client_id:
                    return client_id
        request_id = getattr(context.message, "request_id", None)
        if request_id:
            return str(request_id)
        return f"local_context:{id(fastmcp_context)}"


def create_gemini_advisory_middleware(
    advisory_tools: frozenset[str] | None = None,
) -> GeminiAdvisoryMiddleware:
    """Factory function to create Gemini advisory middleware.

    Args:
        advisory_tools: Tools to advise (defaults to gemini_search)

    Returns:
        Configured middleware instance
    """
    if advisory_tools is None:
        advisory_tools = GEMINI_TOOLS

    return GeminiAdvisoryMiddleware(advisory_tools=advisory_tools)


__all__ = [
    "GEMINI_TOOLS",
    "GEMINI_QUERY_ADVISORY",
    "GeminiAdvisoryMiddleware",
    "create_gemini_advisory_middleware",
]
