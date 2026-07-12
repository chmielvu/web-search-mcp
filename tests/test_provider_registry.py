from __future__ import annotations

import pytest
from pydantic import ValidationError

from kindly_web_search_mcp_server.search.contracts import (
    ProviderTarget,
    QueryBranch,
    WebSearchRequest,
)
from kindly_web_search_mcp_server.search.provider_registry import (
    PROVIDER_ADAPTERS,
    PROVIDER_DEFINITIONS,
)


def test_registry_has_exact_provider_matrix() -> None:
    assert tuple(PROVIDER_DEFINITIONS) == tuple(PROVIDER_ADAPTERS)
    assert len(PROVIDER_DEFINITIONS) == 19
    assert all(definition.search_engine is None for definition in PROVIDER_DEFINITIONS.values())
    assert PROVIDER_DEFINITIONS["qdrant"].requires_embedding is True
    assert ProviderTarget.COMMUNITY in PROVIDER_DEFINITIONS["brave_news"].targets


def test_request_enforces_goal_and_result_window() -> None:
    with pytest.raises(ValidationError):
        WebSearchRequest(query="query", research_goal="")
    with pytest.raises(ValidationError):
        WebSearchRequest(query="query", research_goal="goal", num_results=14)
    with pytest.raises(ValidationError):
        WebSearchRequest(query="query", research_goal="goal", num_results=51)
    for count in (15, 20, 50):
        assert (
            WebSearchRequest(query="query", research_goal="goal", num_results=count).num_results
            == count
        )


def test_query_branch_has_no_redundant_kind() -> None:
    branch = QueryBranch(
        target=ProviderTarget.KEYWORD,
        query="alpha",
        why="test",
        max_results=15,
    )
    assert "kind" not in branch.model_dump()
