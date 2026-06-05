"""Helpers for turning structured fan-out output into rewrite plan branches."""

from __future__ import annotations

from ..utils.diagnostics import Diagnostics
from .query_fanout_client import generate_fanout_branches
from .query_rewrite_models import (
    ClassifierOutput,
    QueryDecompositionOutput,
    QueryVariant,
    SubQuestion,
)


async def build_fanout_branch_variants(
    *,
    query: str,
    classifier: ClassifierOutput,
    research_goal: str | None,
    must_keep_terms: list[str],
    active_provider_names: list[str],
    diagnostics: Diagnostics | None,
    max_branches: int,
) -> tuple[QueryDecompositionOutput | None, list[QueryVariant] | None]:
    fanout = await generate_fanout_branches(
        query=query,
        intent=classifier.intent,
        research_goal=research_goal,
        must_keep_terms=must_keep_terms,
        active_provider_names=active_provider_names,
        routing=classifier.routing.model_dump(),
        max_branches=max_branches,
        diagnostics=diagnostics,
    )
    if not fanout.branches:
        return None, None

    decomposition = QueryDecompositionOutput(
        should_decompose=True,
        rationale=fanout.rationale,
        sub_questions=[
            SubQuestion(
                question=branch.query,
                target=branch.target,
                why=branch.why,
                weight=branch.weight,
                branch_type=branch.branch_type or branch.kind,
                must_keep_terms=branch.must_keep_terms,
                max_results=branch.max_results,
                reason=branch.reason,
            )
            for branch in fanout.branches
        ],
    )
    variants = [
        branch.model_copy(
            update={
                "branch_type": branch.branch_type or branch.kind,
                "reason": branch.reason or branch.why,
            }
        )
        for branch in fanout.branches
    ]
    return decomposition, variants
