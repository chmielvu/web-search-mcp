"""Tests for EntitySpan fields on search and content responses."""

from __future__ import annotations

from kindly_web_search_mcp_server.utils.entity import EntitySpan

from kindly_web_search_mcp_server.models import FetchResult, WebSearchResult


def test_web_search_result_model_accepts_entities() -> None:
    e = EntitySpan(text="foo", label="package", start=0, end=3, confidence=0.8)
    r = WebSearchResult(
        title="t",
        link="https://ex",
        snippet="s",
        entities=[e],
    )
    assert r.entities and r.entities[0].label == "package"


def test_fetch_result_model_accepts_entities() -> None:
    e = EntitySpan(text="bar", label="api_function", start=10, end=13)
    c = FetchResult(
        url="u",
        status="success",
        content="content here",
        window={},
        entities=[e],
    )
    assert c.entities and c.entities[0].text == "bar"
