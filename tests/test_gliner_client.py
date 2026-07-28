"""Tests for the hosted GLiNER2 gateway and pure response normalization."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from kindly_web_search_mcp_server.entity.gliner_client import (
    GLiNER2Client,
    get_gliner_client,
    is_entity_extraction_enabled,
)
from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.settings import settings


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Client:
    response = _Response({"entities": {"package": []}})
    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, *, json):
        self.calls.append({"url": url, "json": json})
        return self.response


def test_main_entity_package_does_not_import_gliner2():
    for module in list(sys.modules):
        if module == "gliner2" or module.startswith("gliner2."):
            del sys.modules[module]
    import kindly_web_search_mcp_server.entity as entity

    assert "gliner2" not in sys.modules
    assert entity.EntitySpan is EntitySpan


def test_content_extraction_remains_opt_in(monkeypatch):
    monkeypatch.delenv("ENTITY_EXTRACTION_ENABLED", raising=False)
    monkeypatch.setattr(settings, "entity_extraction_enabled", False)
    assert is_entity_extraction_enabled() is False
    monkeypatch.setenv("ENTITY_EXTRACTION_ENABLED", "true")
    assert is_entity_extraction_enabled() is True


@pytest.mark.asyncio
async def test_content_gateway_normalizes_grouped_entities_and_offsets(monkeypatch):
    monkeypatch.setenv("ENTITY_EXTRACTION_ENABLED", "true")
    monkeypatch.setattr(settings, "entity_extraction_enabled", True)
    _Client.calls = []
    _Client.response = _Response(
        {
            "results": {
                "entities": {
                    "package": [{"text": "FastAPI", "start": 4, "end": 11, "confidence": 0.91}]
                }
            }
        }
    )
    with patch("kindly_web_search_mcp_server.entity.gliner_client.httpx.AsyncClient", _Client):
        entities = await GLiNER2Client(base_url="http://127.0.0.1:8000").extract_entities(
            "Use FastAPI here"
        )

    assert entities == [
        EntitySpan(text="FastAPI", label="package", start=4, end=11, confidence=0.91)
    ]
    assert _Client.calls[0]["url"] == "http://127.0.0.1:8000/extract"
    assert set(_Client.calls[0]["json"]["entities"]) >= {"package", "version"}
    assert _Client.calls[0]["json"]["entities"]["package"] == (
        "Software package, library, or framework name"
    )
    assert _Client.calls[0]["json"]["include_spans"] is True


@pytest.mark.asyncio
async def test_disabled_content_gateway_makes_no_request(monkeypatch):
    monkeypatch.setenv("ENTITY_EXTRACTION_ENABLED", "false")
    monkeypatch.setattr(settings, "entity_extraction_enabled", False)
    _Client.calls = []
    with patch("kindly_web_search_mcp_server.entity.gliner_client.httpx.AsyncClient", _Client):
        entities = await GLiNER2Client(base_url="http://127.0.0.1:8000").extract_entities("FastAPI")

    assert entities == []
    assert _Client.calls == []


def test_singleton_factory_is_stable(monkeypatch):
    monkeypatch.setattr("kindly_web_search_mcp_server.entity.gliner_client._gliner_client", None)
    first = get_gliner_client()
    second = get_gliner_client()
    assert first is second
