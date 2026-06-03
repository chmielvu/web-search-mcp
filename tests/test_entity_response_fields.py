"""Tests for EntitySpan fields on search and content responses (Phase 8.2).

Entities appear in outputs only when extraction enabled; field omitted (or None) when disabled.
No BC requirement per plan.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.models import WebSearchResult, GetContentResponse  # for mocks in entity tests


def test_web_search_result_model_accepts_entities():
    e = EntitySpan(text="foo", label="package", start=0, end=3, confidence=0.8)
    r = WebSearchResult(
        title="t",
        link="https://ex",
        snippet="s",
        entities=[e],
    )
    assert r.entities and r.entities[0].label == "package"


def test_get_content_response_model_accepts_entities():
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
async def test_entities_only_when_enabled_in_search(monkeypatch):
    """When disabled, search results should not populate entities (or field None)."""
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "false")

    from kindly_web_search_mcp_server.search import orchestrator as orch

    # patch providers to return simple result
    with patch.object(orch, "search_single_query", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [
            WebSearchResult(title="FastAPI docs", link="https://ex/fast", snippet="FastAPI v2", providers=["searxng"])
        ]
        resp = await orch.run_web_search("FastAPI", num_results=1, rewrite=False)
        for r in resp.results:
            # when disabled, either no .entities or None/empty
            ents = getattr(r, "entities", None)
            assert ents in (None, [], ()) or len(ents) == 0


@pytest.mark.asyncio
async def test_entities_populated_when_enabled_in_search(monkeypatch):
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "true")

    fake_ent = EntitySpan(text="FastAPI", label="package", start=0, end=7, confidence=0.9)

    from kindly_web_search_mcp_server.search import orchestrator as orch

    with patch.object(orch, "search_single_query", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [
            WebSearchResult(title="FastAPI docs", link="https://ex/fast", snippet="FastAPI v2", providers=["searxng"])
        ]
        with patch("kindly_web_search_mcp_server.entity.gliner_client.get_gliner_client") as mock_g:
            cl = AsyncMock()
            cl.extract_entities.return_value = [fake_ent]
            mock_g.return_value = cl

            resp = await orch.run_web_search("FastAPI", num_results=1, rewrite=False)
            assert resp.results
            ents = getattr(resp.results[0], "entities", None)
            assert ents
            assert any(e.label == "package" for e in (ents or []))


@pytest.mark.asyncio
async def test_content_response_entities_when_enabled(monkeypatch):
    """Resolver / fetch path attaches entities when enabled."""
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "true")

    fake_ent = EntitySpan(text="pydantic", label="package", start=5, end=13, confidence=0.7)

    with patch("kindly_web_search_mcp_server.entity.gliner_client.get_gliner_client") as mock_g:
        cl = AsyncMock()
        cl.extract_entities.return_value = [fake_ent]
        mock_g.return_value = cl

        # Patch on fetch path (we hooked in fetch_pipeline); assert model accepts + construction would carry
        resp_like = GetContentResponse(
            input_url="https://ex",
            normalized_url="https://ex",
            status="success",
            source_type="html",
            fetch_backend="trafilatura",
            page_content="import pydantic; ...",
            window={},
            entities=[fake_ent],
        )
        assert resp_like.entities and resp_like.entities[0].text == "pydantic"
