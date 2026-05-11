"""FastMCP middleware for Gemini search advisory (non-blocking).

Provides informational guidance for query quality without blocking calls.
Gemini with Google Search grounding is cheap, so this is advisory-only.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger(__name__)

# Tools to provide advisory guidance for
GEMINI_TOOLS = frozenset({"gemini_search"})

# Advisory message for Gemini query best practices - informative, not blocking
GEMINI_QUERY_ADVISORY = """
💡 GEMINI SEARCH: QUERY QUALITY TIPS

Gemini with Google Search grounding provides quick, grounded answers. For best results:

**BEST PRACTICES:**
1. **Be specific** — "React 19 concurrent rendering changes" beats "React 19"
2. **Single focus** — one clear question per call
3. **Use exact terms** — error messages, API names, version numbers
4. **Add recency hints** — "2024", "latest", "recent" when needed
5. **State information need** — what exactly are you looking for?

**EXAMPLES:**
✅ "How does React 19's use() hook differ from useEffect for data fetching?"
✅ "Python 3.12 type parameter syntax changes from 3.11"
❌ "Tell me about React" (too broad)
❌ "What's new in Python and Rust and Go?" (multiple topics)

**Note:** This is informational only — your call proceeds normally.
"""


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

        # Log advisory message (non-blocking, informational)
        session_id = getattr(context.message, 'request_id', 'default')
        call_count = self._call_counts.get(session_id, 0) + 1
        self._call_counts[session_id] = call_count

        if call_count <= 2:  # Only log advisory for first couple calls
            logger.info(
                f"Gemini advisory for {tool_name} "
                f"(session={session_id}, call={call_count}) - "
                f"query tips available but call proceeds"
            )

        # Call proceeds normally - advisory is informational only
        return await call_next(context)


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