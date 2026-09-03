"""Planner-side related-seed query expansion consumer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..analytics.graph_store import load_latest_graph_index
from .normalize import normalize_query


@dataclass(frozen=True, slots=True)
class GraphExpansionDecision:
    status: str  # disabled, applied, no_capacity, no_match, unavailable, error
    generation_id: str | None
    base_seed_queries: tuple[str, ...]
    effective_seed_queries: tuple[str, ...]
    related_queries: tuple[str, ...]
    error_type: str | None = None
    source_fingerprint: str | None = None
    artifact_age_seconds: float | None = None
    matched_query: str | None = None
    candidate_support_counts: tuple[tuple[str, int], ...] = ()
    dropped_candidates: tuple[tuple[str, str], ...] = ()


def expand_seed_queries(
    *,
    normalized_query: str,
    base_seed_queries: tuple[str, ...],
    enabled: bool,
    max_related_queries: int,
    max_age_seconds: float,
    artifact_path: str | None = None,
) -> GraphExpansionDecision:
    """Return bounded, support-qualified graph seeds without touching providers or LLMs."""
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
        index = load_latest_graph_index(
            sqlite_path=artifact_path,
            max_age_seconds=max_age_seconds,
        )
        if index is None:
            return GraphExpansionDecision(
                status="unavailable",
                generation_id=None,
                base_seed_queries=base_seed_queries,
                effective_seed_queries=base_seed_queries,
                related_queries=(),
            )
        matched_query = normalize_query(normalized_query)
        candidates = index.neighbors.get(matched_query, ())
        support_map = index.neighbor_supports.get(matched_query, {})
        support_counts = tuple((candidate, support_map.get(candidate, 0)) for candidate in candidates)
        artifact_age = (datetime.now(timezone.utc) - index.built_at).total_seconds()
        common = {
            "artifact_age_seconds": artifact_age,
            "candidate_support_counts": support_counts,
            "generation_id": index.generation_id,
            "matched_query": matched_query,
            "source_fingerprint": index.source_fingerprint,
        }
        if not candidates:
            return GraphExpansionDecision(
                status="no_match",
                base_seed_queries=base_seed_queries,
                effective_seed_queries=base_seed_queries,
                related_queries=(),
                **common,
            )

        seen = {seed.casefold() for seed in base_seed_queries if seed}
        related: list[str] = []
        dropped: list[tuple[str, str]] = []
        max_allowed = min(max_related_queries, 4 - len(base_seed_queries))
        for candidate in candidates:
            normalized_candidate = normalize_query(candidate)
            if not normalized_candidate:
                dropped.append((candidate, "empty"))
            elif normalized_candidate == matched_query:
                dropped.append((candidate, "source_query"))
            elif normalized_candidate.casefold() in seen:
                dropped.append((candidate, "duplicate"))
            elif support_map.get(candidate, 0) < 2:
                dropped.append((candidate, "insufficient_support"))
            elif len(related) >= max_allowed:
                dropped.append((candidate, "capacity"))
            else:
                seen.add(normalized_candidate.casefold())
                related.append(normalized_candidate)
        effective = base_seed_queries + tuple(related)
        return GraphExpansionDecision(
            status="applied" if related else "no_match",
            base_seed_queries=base_seed_queries,
            effective_seed_queries=effective,
            related_queries=tuple(related),
            dropped_candidates=tuple(dropped),
            **common,
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
