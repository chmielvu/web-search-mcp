"""Tests for the Europeana academic provider (httpx.MockTransport + env)."""

from __future__ import annotations

import httpx
import pytest

from kindly_web_search_mcp_server.search.academic.academic_europeana import search_europeana


def _europeana_item(guid: str, title: str, dc_creator: list[str] | None = None) -> dict:
    item: dict = {
        "guid": guid,
        "link": f"https://www.europeana.eu/item{guid}",
        "title": [title],
        "edmPreview": ["https://thumb.example/preview.jpg"],
        "dataProvider": ["Polona"],
        "year": ["1900"],
    }
    if dc_creator:
        item["dcCreator"] = dc_creator
    return item


_EUROPEANA_PAYLOAD = {
    "items": [
        _europeana_item("/123/abc", "Słownik polsko-łaciński", ["Jan Mączyński"]),
        _europeana_item("/456/def", "Mapa Królestwa Polskiego"),
    ]
}


async def test_search_europeana_skips_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No EUROPEANA_API_KEY -> [] with zero network activity."""
    monkeypatch.delenv("EUROPEANA_API_KEY", raising=False)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be touched without an API key")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_europeana("test", limit=2, http_client=client)

    assert papers == []


async def test_search_europeana_returns_papers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EUROPEANA_API_KEY", "test-key")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "test-key"
        assert "wskey" not in request.url.params
        assert request.url.params["query"] == "test"
        assert request.url.params["rows"] == "4"  # min(2*2, 100)
        assert request.url.params["qf"] == "COUNTRY:poland"
        assert request.url.params["profile"] == "standard"
        return httpx.Response(200, json=_EUROPEANA_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_europeana("test", limit=2, http_client=client)

    assert len(papers) == 2
    first = papers[0]
    assert first.title == "Słownik polsko-łaciński"
    assert first.authors == ["Jan Mączyński"]
    assert first.year == 1900
    assert first.venue == "Polona"
    assert first.citations is None
    assert first.url == "/123/abc"
    assert first.source == "europeana"
    assert first.source_id == "/123/abc"
    assert first.is_open_access is True

    second = papers[1]
    assert second.title == "Mapa Królestwa Polskiego"
    assert second.authors == []
    assert second.year == 1900


async def test_search_europeana_returns_empty_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EUROPEANA_API_KEY", "bad-key")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_europeana("test", limit=2, http_client=client)

    assert papers == []


async def test_search_europeana_rows_capped_at_100(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EUROPEANA_API_KEY", "test-key")
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["rows"] = request.url.params["rows"]
        return httpx.Response(200, json={"items": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await search_europeana("test", limit=60, http_client=client)

    assert seen["rows"] == "100"
