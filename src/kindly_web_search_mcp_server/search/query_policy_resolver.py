"""Query routing resolver - directly uses heuristic classification.

No HF Space backend - simplified to direct precision signal detection.
"""

from __future__ import annotations

from ..utils.diagnostics import Diagnostics
from .query_policy import RewritePolicy, classify_search_query


async def resolve_query_routing(
    query: str,
    *,
    diagnostics: Diagnostics | None = None,
) -> RewritePolicy:
    """Resolve query routing policy based on precision signal detection.

    Simple: detect literals → bypass, otherwise → expand.
    No intent classification, no HF Space calls.

    Args:
        query: Raw query string
        diagnostics: Optional diagnostics emitter

    Returns:
        RewritePolicy with bypass/expand mode
    """
    policy = classify_search_query(query)

    if diagnostics:
        diagnostics.emit(
            "query_policy.resolved",
            "Resolved query routing via precision signal detection",
            {"mode": policy.mode, "must_keep_terms": policy.must_keep_terms},
        )

    return policy
