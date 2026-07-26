"""Unit tests for 5-variant query rewrite pipeline and prompt guidance modules."""

from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.prompts.query_rewrite import (
    DEFAULT_SPECIALIZED_GUIDANCE,
    REWRITE_SYSTEM_MESSAGE,
    REWRITE_USER_TEMPLATE,
    SPECIALIZED_REWRITE_GUIDANCE,
)
from kindly_web_search_mcp_server.search.planning import (
    _RewriteQueries,
    _rewrite_queries,
)


def test_specialized_guidance_exports() -> None:
    assert "5 effective search queries" in REWRITE_SYSTEM_MESSAGE
    assert "<SPECIALIZED_QUERY_RULES>" in REWRITE_USER_TEMPLATE
    assert DEFAULT_SPECIALIZED_GUIDANCE in SPECIALIZED_REWRITE_GUIDANCE.values()
    assert "GitHub" in SPECIALIZED_REWRITE_GUIDANCE["ai_coding_and_infrastructure"]
    assert "Reddit" in SPECIALIZED_REWRITE_GUIDANCE["social_media"]
    assert "breaking news" in SPECIALIZED_REWRITE_GUIDANCE["news"]
    assert SPECIALIZED_REWRITE_GUIDANCE["general"] == DEFAULT_SPECIALIZED_GUIDANCE


def test_rewrite_queries_schema_5_items() -> None:
    model = _RewriteQueries.model_validate_json(
        '{"queries": ["k1", "k2", "k3", "neural", "specialized"]}'
    )
    assert len(model.queries) == 5
    assert model.queries[4] == "specialized"


@pytest.mark.asyncio
async def test_rewrite_queries_handles_4_items_resiliently(monkeypatch) -> None:
    """If model returns 4 queries, _rewrite_queries appends the 4th to form 5."""

    captured = {}

    class DummyRouter:
        async def complete_json(self, **kwargs):
            captured.update(kwargs)

            class Generation:
                content = '{"queries": ["q1", "q2", "q3", "q4"]}'
                model_used = "dummy-model"
                input_tokens = 10
                output_tokens = 20

            return Generation()

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.planning.build_worker_router",
        lambda: DummyRouter(),
    )

    parsed, meta = await _rewrite_queries(
        query="test query",
        research_goal="test goal",
        terms=(),
        suggestions=(),
        correction=None,
        current_year="2026",
        intent="ai_coding_and_infrastructure",
    )
    assert len(parsed.queries) == 5
    assert parsed.queries[4] == "q4"
    assert captured["reasoning_effort"] == "none"
