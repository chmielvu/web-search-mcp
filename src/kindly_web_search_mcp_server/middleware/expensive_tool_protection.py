"""FastMCP middleware for expensive tool protection.

Implements the "Think first, then call expensive tool" pattern:
- Blocks first attempt to call expensive tools
- Returns a steering message with query-writing best practices
- Allows subsequent calls through

Uses FastMCP's Middleware class and ToolError for blocking.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)

# Tools considered expensive (require query quality check)
EXPENSIVE_TOOLS = frozenset({"perplexity_search"})

# Session timeout in seconds (reset attempt count after this)
SESSION_TIMEOUT_SECONDS = 300  # 5 minutes

# Best practices message for query writing - concise but dense
QUERY_QUALITY_STEERING_MESSAGE = """
⚠️ PERPLEXITY SONAR: EXPENSIVE RESOURCE — QUERY QUALITY REQUIRED

Perplexity Sonar is rate-limited and costly. Before retrying, refine your query:

**MANDATORY RULES:**
1. **Add 2-3 words of context** — transforms vague queries into precise searches
   - ❌ "AI trends" → ✅ "three most significant generative AI applications in healthcare in 2024"
2. **Search-friendly terminology** — use terms appearing on authoritative sites
3. **Single topic per query** — multi-part requests degrade quality
4. **Explicit information need** — state exactly what you're looking for
5. **Fallback clause** — add "If not found, state clearly" to handle gaps

**EXAMPLES:**
✅ GOOD: "Compare energy efficiency of heat pumps vs HVAC for residential use in cold climates"
✅ GOOD: "Peer-reviewed research on mRNA vaccine technology advances since 2023"
❌ BAD: "Tell me about AI" (vague, no context)
❌ BAD: "What's new in tech and climate and finance?" (multiple topics)

**ACTION:**
Refine your query with: domain context + specific need + single focus.
Then call `perplexity_search` again. Low-quality queries waste expensive resources.
"""


@dataclass
class SessionState:
    """Tracks attempt state for a client session."""
    attempts: dict[str, int] = field(default_factory=dict)  # tool_name -> count
    last_activity: float = field(default_factory=time.time)


class ExpensiveToolProtectionMiddleware(Middleware):
    """Middleware that enforces query quality for expensive tools.

    Implements the "think first" pattern:
    - First call: Raises ToolError with steering message
    - Second call: Allows through (agent has refined query)

    This forces the calling agent to analyze and improve their query
    before consuming expensive API resources.
    """

    def __init__(
        self,
        protected_tools: frozenset[str] = EXPENSIVE_TOOLS,
        block_first_attempt: bool = True,
        session_timeout_seconds: float = SESSION_TIMEOUT_SECONDS,
    ):
        """Initialize middleware.

        Args:
            protected_tools: Set of tool names to protect
            block_first_attempt: If True, block first call; if False, allow first
            session_timeout_seconds: Seconds before session resets
        """
        self.protected_tools = protected_tools
        self.block_first_attempt = block_first_attempt
        self.session_timeout_seconds = session_timeout_seconds
        # In-memory session tracking (use Redis/database in production)
        self._sessions: dict[str, SessionState] = defaultdict(SessionState)

    def _get_session_id(self, context: MiddlewareContext) -> str:
        """Extract session identifier from context.

        Uses available context information. In production, should use
        proper client session tracking from HTTP headers or MCP context.
        """
        # FastMCP context may have request_id or client info
        # For now, use a simple approach
        request_id = getattr(context.message, 'request_id', None)
        if request_id:
            return str(request_id)
        return "default_session"

    def _is_session_expired(self, state: SessionState) -> bool:
        """Check if session has timed out."""
        return time.time() - state.last_activity > self.session_timeout_seconds

    def _get_attempt_count(self, session_id: str, tool_name: str) -> int:
        """Get current attempt count for a tool in a session."""
        state = self._sessions[session_id]

        if self._is_session_expired(state):
            state.attempts.clear()
            state.last_activity = time.time()
            return 0

        return state.attempts.get(tool_name, 0)

    def _increment_attempt(self, session_id: str, tool_name: str) -> int:
        """Increment and return new attempt count."""
        state = self._sessions[session_id]
        state.last_activity = time.time()
        current = state.attempts.get(tool_name, 0)
        state.attempts[tool_name] = current + 1
        return current + 1

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: Any,
    ) -> Any:
        """Intercept tool calls and enforce query quality.

        Args:
            context: MiddlewareContext with tool call details
            call_next: Next handler in chain

        Returns:
            Tool result if allowed through

        Raises:
            ToolError: If blocked with steering message
        """
        tool_name = context.message.name

        # Only protect specific tools
        if tool_name not in self.protected_tools:
            return await call_next(context)

        session_id = self._get_session_id(context)
        attempt_count = self._get_attempt_count(session_id, tool_name)

        # Block first attempt if configured
        if self.block_first_attempt and attempt_count == 0:
            # Record this blocked attempt
            self._increment_attempt(session_id, tool_name)

            logger.info(
                f"Blocked first attempt for {tool_name} "
                f"(session={session_id}), returning steering message"
            )

            # Raise ToolError - this returns error to client
            # The message becomes tool-call failure context for the agent
            raise ToolError(QUERY_QUALITY_STEERING_MESSAGE)

        # Allow through - record this successful attempt
        self._increment_attempt(session_id, tool_name)
        logger.debug(f"Allowing call through for {tool_name} (attempt {attempt_count + 1})")

        return await call_next(context)


def create_expensive_tool_middleware(
    protected_tools: frozenset[str] | None = None,
    block_first_attempt: bool = True,
) -> ExpensiveToolProtectionMiddleware:
    """Factory function to create the middleware.

    Args:
        protected_tools: Tools to protect (defaults to perplexity_search)
        block_first_attempt: Whether to block the first attempt

    Returns:
        Configured middleware instance
    """
    if protected_tools is None:
        protected_tools = EXPENSIVE_TOOLS

    return ExpensiveToolProtectionMiddleware(
        protected_tools=protected_tools,
        block_first_attempt=block_first_attempt,
    )


__all__ = [
    "EXPENSIVE_TOOLS",
    "QUERY_QUALITY_STEERING_MESSAGE",
    "ExpensiveToolProtectionMiddleware",
    "create_expensive_tool_middleware",
]