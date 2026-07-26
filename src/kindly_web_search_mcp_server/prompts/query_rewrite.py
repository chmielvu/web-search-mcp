"""Query rewrite prompt constants.

The live prompt is assembled and executed in search/planning.py.
This module re-exports the template strings and specialized prompt guidance modules for inspection and testing.
"""

from __future__ import annotations

from kindly_web_search_mcp_server.search.planning import (
    _DEFAULT_SPECIALIZED_GUIDANCE as DEFAULT_SPECIALIZED_GUIDANCE,
    _REWRITE_SYSTEM as REWRITE_SYSTEM_MESSAGE,
    _REWRITE_USER as REWRITE_USER_TEMPLATE,
    _SPECIALIZED_REWRITE_GUIDANCE as SPECIALIZED_REWRITE_GUIDANCE,
)


__all__ = [
    "DEFAULT_SPECIALIZED_GUIDANCE",
    "REWRITE_SYSTEM_MESSAGE",
    "REWRITE_USER_TEMPLATE",
    "SPECIALIZED_REWRITE_GUIDANCE",
]
