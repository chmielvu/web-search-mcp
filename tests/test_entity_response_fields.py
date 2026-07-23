"""Tests for EntitySpan fields on search and content responses."""

from __future__ import annotations

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
