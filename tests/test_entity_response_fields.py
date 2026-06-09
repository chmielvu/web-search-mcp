"""Tests for EntitySpan fields on search and content responses."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.models import GetContentResponse, WebSearchResult


def test_web_search_result_model_accepts_entities() -> None:
    e = EntitySpan(text="foo", label="package", start=0, end=3, confidence=0.8)
    r = WebSearchResult(
        title="t",
        link="https://ex",
        snippet="s",
        entities=[e],
    )
    assert r.entities and r.entities[0].label == "package"


def test_get_content_response_model_accepts_entities() -> None:
    e = EntitySpan(text="bar", label="api_function", start=10, end=13)
    c = GetContentResponse(
        input_url="u",
        normalized_url="u",
        status="success",
        source_type="html",
        fetch_backend="http",
        page_content="content here",
        window={},
        entities=[e],
    )
    assert c.entities and c.entities[0].text == "bar"


@pytest.mark.asyncio
async def test_entities_only_when_enabled_in_search(monkeypatch) -> None:
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "false")

    from kindly_web_search_mcp_server.search.branch_executor import BranchExecutionBatch
    from kindly_web_search_mcp_server.search.pipeline import run_search_pipeline
    from kindly_web_search_mcp_server.search.query_rewrite_models import QueryVariant
    from kindly_web_search_mcp_server.search.understanding.models import (
        QueryUnderstandingResult,
    )
    from kindly_web_search_mcp_server.settings import settings

    batch = BranchExecutionBatch(
        result_lists=[
            [
                WebSearchResult(
                    title="FastAPI docs",
                    link="https://ex/fast",
                    snippet="FastAPI v2",
                    providers=["searxng"],
                )
            ]
        ],
        branch_queries=["FastAPI"],
        branch_providers=[["searxng"]],
        list_weights=[1.0],
        branch_metadata=[{"branch_index": 0}],
    )

    with (
        patch.object(settings, "entity_extraction_enabled", False),
        patch.object(settings, "web_results_index_enabled", False),
        patch(
            "kindly_web_search_mcp_server.search.pipeline.resolve_query_understanding",
            new_callable=AsyncMock,
        ) as mock_understanding,
        patch(
            "kindly_web_search_mcp_server.search.pipeline.build_rewrite_variants",
            new_callable=AsyncMock,
        ) as mock_rewrite,
        patch(
            "kindly_web_search_mcp_server.search.pipeline.execute_search_branches",
            new_callable=AsyncMock,
        ) as mock_execute,
        patch(
            "kindly_web_search_mcp_server.search.pipeline.inject_result_memory_candidates",
            new_callable=AsyncMock,
        ) as mock_memory_inject,
        patch(
            "kindly_web_search_mcp_server.search.pipeline.store_result_memory_results",
            new_callable=AsyncMock,
        ) as _mock_memory_store,
        patch(
            "kindly_web_search_mcp_server.search.pipeline.rerank_results",
            new_callable=AsyncMock,
        ) as mock_rerank,
    ):
        mock_understanding.return_value = QueryUnderstandingResult(
            intent="general",
            confidence=0.91,
            rationale="general web search",
        )
        mock_rewrite.return_value = (
            [
                QueryVariant(
                    kind="original",
                    target="keyword",
                    query="FastAPI",
                    why="original",
                    weight=1.0,
                )
            ],
            "vercel",
        )
        mock_execute.return_value = batch
        mock_memory_inject.return_value = (
            batch.result_lists,
            batch.list_weights,
            None,
            [],
        )
        mock_rerank.side_effect = lambda _query, candidates, top_k, **kwargs: candidates[
            :top_k
        ]

        resp = await run_search_pipeline(
            "FastAPI",
            num_results=1,
            rewrite=True,
            diagnostics=None,
            providers=["searxng"],
            research_goal="testing",
            search_options=None,
        )

    assert resp.results[0].entities in (None, [], ())
    assert mock_understanding.awaited
    assert mock_execute.awaited


@pytest.mark.asyncio
async def test_entity_extraction_runs_when_enabled_in_search(monkeypatch) -> None:
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "true")

    from kindly_web_search_mcp_server.search.finalize_results import (
        maybe_extract_entities,
    )
    from kindly_web_search_mcp_server.settings import settings

    fake_ent = EntitySpan(
        text="FastAPI",
        label="package",
        start=0,
        end=7,
        confidence=0.9,
    )
    with (
        patch.object(settings, "entity_extraction_enabled", True),
        patch(
            "kindly_web_search_mcp_server.search.finalize_results.extract_entities",
            new_callable=AsyncMock,
        ) as mock_extract,
    ):
        mock_extract.return_value = [fake_ent]

        result = WebSearchResult(
            title="FastAPI docs",
            link="https://ex/fast",
            snippet="FastAPI v2",
            providers=["searxng"],
        )
        out = await maybe_extract_entities(query="FastAPI", results=[result])

    assert len(out) == 1
    assert mock_extract.awaited
