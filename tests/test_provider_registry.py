from __future__ import annotations
import inspect

import pytest
from pydantic import ValidationError

from kindly_web_search_mcp_server.search.contracts import (
    BranchRole,
    QueryBranch,
    WebSearchRequest,
)
from kindly_web_search_mcp_server.search.provider_registry import (
    PROVIDER_ADAPTERS,
    PROVIDER_DEFINITIONS,
)


def test_registry_has_exact_provider_matrix() -> None:
    assert tuple(PROVIDER_DEFINITIONS) == tuple(PROVIDER_ADAPTERS)
    assert len(PROVIDER_DEFINITIONS) == 16
    assert PROVIDER_DEFINITIONS["qdrant"].requires_embedding is True
    assert "brightdata_bing" in PROVIDER_DEFINITIONS
    assert "brightdata_yandex" in PROVIDER_DEFINITIONS
    assert "langsearch" in PROVIDER_DEFINITIONS
    # Public-code providers were removed from web_search; use code_search.
    assert "github" not in PROVIDER_DEFINITIONS
    for removed in (
        "jina",
        "grok_xai",
        "hackernews",
        "reddit",
        "telegram",
        "brave_news",
    ):
        assert removed not in PROVIDER_DEFINITIONS
    assert "sourcegraph" not in PROVIDER_DEFINITIONS
    assert "gitlab" not in PROVIDER_DEFINITIONS
    assert "github_graphql" not in PROVIDER_DEFINITIONS
    assert PROVIDER_DEFINITIONS["langsearch"].adapter_module == "providers.langsearch"
    assert PROVIDER_DEFINITIONS["langsearch"].adapter_function == "search_langsearch"
    assert not hasattr(PROVIDER_DEFINITIONS["ddg"], "targets")
    assert PROVIDER_DEFINITIONS["exa"].adapter_module == "providers.exa"
    assert PROVIDER_DEFINITIONS["exa"].adapter_function == "search_exa"
    assert inspect.iscoroutinefunction(PROVIDER_ADAPTERS["exa"])


def test_brightdata_default_timeout_uses_retrieve_budget() -> None:
    from kindly_web_search_mcp_server.search.provider_catalog import (
        brightdata_provider_call_timeout_seconds,
    )
    from kindly_web_search_mcp_server.settings import settings

    outer = PROVIDER_DEFINITIONS["brightdata"].default_timeout_seconds
    assert outer == brightdata_provider_call_timeout_seconds()
    assert outer == settings.search_retrieve_budget_seconds
    assert outer > 10.0


def test_brightdata_requires_api_key_and_explicit_zone() -> None:
    definition = PROVIDER_DEFINITIONS["brightdata"]
    assert definition.all_of == ("BRIGHTDATA_API_KEY",)
    assert definition.any_of == ("BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_ZONE")


def test_request_enforces_goal_and_result_window() -> None:
    with pytest.raises(ValidationError):
        WebSearchRequest(query="query", research_goal="")
    with pytest.raises(ValidationError):
        WebSearchRequest(query="query", research_goal="goal", num_results=16)
    assert WebSearchRequest(query="query", research_goal="goal").num_results == 15


def test_query_branch_uses_role_and_provider_names() -> None:
    branch = QueryBranch(
        role=BranchRole.FREE,
        query="alpha",
        provider_names=("ddg", "qdrant", "searxng", "degoog"),
        why="test",
        max_results=15,
    )
    dumped = branch.model_dump()
    assert dumped["role"] == "free"
    assert dumped["provider_names"] == ("ddg", "qdrant", "searxng", "degoog")


def test_semantic_tavily_round_robin(monkeypatch: pytest.MonkeyPatch) -> None:
    from kindly_web_search_mcp_server.search import provider_registry

    monkeypatch.setattr(provider_registry, "_SEMANTIC_TAVILY_RR_CURSOR", 0)
    assert provider_registry.select_semantic_tavily_provider(("tavily", "langsearch")) == "tavily"
    assert (
        provider_registry.select_semantic_tavily_provider(("tavily", "langsearch")) == "langsearch"
    )
