"""Analytics producers — typed observability event emitters and persistence helpers.

Public emitters:
- :func:`emit_observability_event` — structured log event with run-key linkage
- :func:`emit_tool_observability_event` — bounded MCP tool lifecycle event

Internal persistence helpers (_persist_*, _insert_tool_call_analytics,
_persist_tool_output_items, _persist_analytics_event) live in the sibling
modules split by concern:

- :mod:`.events` — emitters and the tool-call sink
- :mod:`.quick_search` — quick_web_search / gemini_search persistence
- :mod:`.code_search` — code_search persistence
- :mod:`.content` — fetch / content persistence

The public emitters are re-exported here so callers import one stable path:

``from kindly_web_search_mcp_server.analytics.producers import emit_observability_event``
"""

from __future__ import annotations

from .events import emit_observability_event, emit_tool_observability_event

__all__ = [
    "emit_observability_event",
    "emit_tool_observability_event",
]
