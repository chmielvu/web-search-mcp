from __future__ import annotations

import pytest
from pydantic import ValidationError

from kindly_web_search_mcp_server.search.contracts import (
    BranchOutcome,
    BranchRole,
    QueryBranch,
    SearchPlan,
    WebSearchRequest,
)
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.understanding.models import QueryUnderstandingResult


def test_branch_role_has_exact_six_values() -> None:
    assert tuple(BranchRole) == (
        BranchRole.ORIGINAL_FREE,
        BranchRole.PAID_BRAVE,
        BranchRole.PAID_GOOGLE,
        BranchRole.PAID_OTHER,
        BranchRole.NEURAL,
        BranchRole.SPECIALIZED,
    )


def test_query_branch_requires_role_and_provider_names() -> None:
    branch = QueryBranch(
        role=BranchRole.PAID_GOOGLE,
        query="alpha beta",
        provider_names=("serper",),
        why="paid google",
        support_terms=("beta",),
        max_results=15,
    )
    assert branch.provider_names == ("serper",)
    assert branch.support_terms == ("beta",)
    assert "target" not in branch.model_dump()
    assert "weight" not in branch.model_dump()
    assert "must_keep_terms" not in branch.model_dump()

    empty = QueryBranch(
        role=BranchRole.SPECIALIZED,
        query="alpha",
        provider_names=(),
        max_results=15,
    )
    assert empty.provider_names == ()


def test_search_plan_create_preserves_relevance_query_and_options() -> None:
    understanding = QueryUnderstandingResult(
        intent="general",
        confidence=0.9,
        rationale="test",
    )
    options = SearchOptions(result_offset=3)
    branch = QueryBranch(
        role=BranchRole.ORIGINAL_FREE,
        query="normalized query",
        provider_names=("ddg",),
        max_results=20,
    )
    plan = SearchPlan.create(
        normalized_query="normalized query",
        relevance_query="relevance text",
        understanding=understanding,
        options=options,
        provider_arguments={"ddg": {}},
        branches=[branch],
        policy_version="1.0",
    )
    assert plan.relevance_query == "relevance text"
    assert plan.options.result_offset == 3
    assert plan.branches[0].role is BranchRole.ORIGINAL_FREE
    assert not hasattr(plan, "selected_provider_names")


def test_branch_outcome_dropped_quota_flags() -> None:
    branch = QueryBranch(
        role=BranchRole.NEURAL,
        query="q",
        provider_names=("gemma",),
        max_results=15,
    )
    outcome = BranchOutcome(branch=branch)
    assert "deadline_reached" not in outcome.__dataclass_fields__
    assert "cancelled" not in outcome.__dataclass_fields__


def test_request_enforces_goal_and_result_window() -> None:
    with pytest.raises(ValidationError):
        WebSearchRequest(query="query", research_goal="")
    with pytest.raises(ValidationError):
        WebSearchRequest(query="query", research_goal="goal", num_results=14)
