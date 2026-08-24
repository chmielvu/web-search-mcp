"""Planner-side related-seed query expansion consumer."""

from __future__ import annotations

from dataclasses import dataclass

from ..analytics.graph_feedback import load_latest_graph_index
from .normalize import normalize_query


@dataclass(frozen=True, slots=True)
class GraphExpansionDecision:
    status: str  # disabled, applied, no_capacity, no_match, stale, unavailable, error
    generation_id: str | None
    base_seed_queries: tuple[str, ...]
    effective_seed_queries: tuple[str, ...]
    related_queries: tuple[str, ...]
    error_type: str | None = None


def expand_seed_queries(
    *,
    normalized_query: str,
    base_seed_queries: tuple[str, ...],
    enabled: bool,
    max_related_queries: int,
    max_age_seconds: float,
    db_path: str | None = None,
) -> GraphExpansionDecision:
    """Synchronously determine related query seed additions for planner consumption."""
    if not enabled:
        return GraphExpansionDecision(
            status="disabled",
            generation_id=None,
            base_seed_queries=base_seed_queries,
            effective_seed_queries=base_seed_queries,
            related_queries=(),
        )

    if len(base_seed_queries) >= 4:
        return GraphExpansionDecision(
            status="no_capacity",
            generation_id=None,
            base_seed_queries=base_seed_queries,
            effective_seed_queries=base_seed_queries,
            related_queries=(),
        )

    if max_related_queries <= 0 or max_age_seconds < 0:
        return GraphExpansionDecision(
            status="unavailable",
            generation_id=None,
            base_seed_queries=base_seed_queries,
            effective_seed_queries=base_seed_queries,
            related_queries=(),
        )

    try:
        index = load_latest_graph_index(db_path=db_path, max_age_seconds=max_age_seconds)
        if index is None:
            return GraphExpansionDecision(
                status="unavailable",
                generation_id=None,
                base_seed_queries=base_seed_queries,
                effective_seed_queries=base_seed_queries,
                related_queries=(),
            )

        q_norm = normalize_query(normalized_query)
        candidates = index.neighbors.get(q_norm, ())
        if not candidates:
            return GraphExpansionDecision(
                status="no_match",
                generation_id=index.generation_id,
                base_seed_queries=base_seed_queries,
                effective_seed_queries=base_seed_queries,
                related_queries=(),
            )

        seen = {s.casefold() for s in base_seed_queries if s}
        appended: list[str] = []
        remaining_capacity = 4 - len(base_seed_queries)
        max_allowed = min(max_related_queries, remaining_capacity)

        for cand in candidates:
            cand_clean = normalize_query(cand)
            cand_key = cand_clean.casefold()
            if not cand_clean or cand_key in seen:
                continue
            seen.add(cand_key)
            appended.append(cand_clean)
            if len(appended) >= max_allowed:
                break

        if not appended:
            return GraphExpansionDecision(
                status="no_match",
                generation_id=index.generation_id,
                base_seed_queries=base_seed_queries,
                effective_seed_queries=base_seed_queries,
                related_queries=(),
            )

        effective = base_seed_queries + tuple(appended)
        return GraphExpansionDecision(
            status="applied",
            generation_id=index.generation_id,
            base_seed_queries=base_seed_queries,
            effective_seed_queries=effective,
            related_queries=tuple(appended),
        )
    except Exception as exc:
        return GraphExpansionDecision(
            status="error",
            generation_id=None,
            base_seed_queries=base_seed_queries,
            effective_seed_queries=base_seed_queries,
            related_queries=(),
            error_type=type(exc).__name__,
        )
