"""Query routing resolver - directly uses heuristic classification.

NOTE: Current routing is a stub — will be revisited as part of the unified
query understanding redesign (see plans/TAXONOMY.md).
"""

from __future__ import annotations

from ..utils.diagnostics import Diagnostics
from .query_policy import RewritePolicy, classify_search_query


async def resolve_query_routing(
    query: str,
    *,
    diagnostics: Diagnostics | None = None,
    entities: list | None = None,
) -> RewritePolicy:
    """Resolve query routing policy based on precision signal detection.

    Entities (GLiNER on original query only) are forwarded to augment must-keep.
    """
    policy = classify_search_query(query, entities=entities)

    if diagnostics:
        diagnostics.emit(
            "query_policy.resolved",
            "Resolved query routing via precision signal detection",
            {"mode": policy.mode, "must_keep_terms": policy.must_keep_terms},
        )

    return policy
