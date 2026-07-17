"""Query rewrite prompt constants.

The live prompt is assembled and executed in search/planning.py.
This module re-exports the template strings for inspection and testing.
"""

from __future__ import annotations

from kindly_web_search_mcp_server.search.planning import (
    _REWRITE_SYSTEM as REWRITE_SYSTEM_MESSAGE,
    _REWRITE_USER as REWRITE_USER_TEMPLATE,
)

__all__ = ["REWRITE_SYSTEM_MESSAGE", "REWRITE_USER_TEMPLATE"]
