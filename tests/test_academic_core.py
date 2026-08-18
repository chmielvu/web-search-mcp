"""Tests for the CORE academic search provider."""

from __future__ import annotations

from contextlib import contextmanager
from unittest import mock

import httpx

import kindly_web_search_mcp_server.search.academic.academic_core as core_mod
from kindly_web_search_mcp_server.search.academic.academic_core import (
    _normalize_core,
    search_core,
)

CORE_RESULT = {
    "id": 99887766,
    "title": "A CORE research paper",
    "abstract": "Open access research abstract.",
    "yearPublished": 2019,
    "publisher": "Example Repository",
    "downloadUrl": "https://core.ac.uk/download/99887766.pdf",
    "doi": "10.1234/core.example",
    "authors": [
        "Alice Example",
        {"name": "Bob Sample"},
        {"id": 5, "name": "Carol Data"},
    ],
    "hasFullText": True,
}


@contextmanager
def _patched_async_client(handler) -> mock._patch:
    """Patch the provider's httpx.AsyncClient with a MockTransport-backed one."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    with mock.patch(
        "kindly_web_search_mcp_server.search.academic.academic_core.httpx.AsyncClient",
        return_value=client,
    ):
        yield client


def test_normalize_core() -> None:
    paper = _normalize_core(CORE_RESULT)
    assert paper is not None
    assert paper.title == "A CORE research paper"
    assert paper.authors == ["Alice Example", "Bob Sample", "Carol Data"]
    assert paper.abstract == "Open access research abstract."
    assert paper.year == 2019
    assert paper.venue == "Example Repository"
    assert paper.pdf_url == "https://core.ac.uk/download/99887766.pdf"
    assert paper.url == "https://core.ac.uk/download/99887766.pdf"
    assert paper.external_ids == {
        "DOI": "10.1234/core.example",
        "CORE": "99887766",
    }
    assert paper.is_open_access is True

    assert _normalize_core({}) is None


async def test_search_core_no_key_no_network(monkeypatch) -> None:
    monkeypatch.setattr(core_mod, "_core_api_key", "")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no network call expected without CORE_API_KEY")

    with _patched_async_client(handler):
        results = await search_core("transformers")

    assert results == []


async def test_search_core_parses_results(monkeypatch) -> None:
    monkeypatch.setattr(core_mod, "_core_api_key", "core_test_key")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": [CORE_RESULT]})

    with _patched_async_client(handler):
        results = await search_core("transformers", limit=5)

    assert len(results) == 1
    paper = results[0]
    assert paper.title == "A CORE research paper"
    assert paper.authors == ["Alice Example", "Bob Sample", "Carol Data"]
    assert paper.year == 2019
    assert paper.external_ids["DOI"] == "10.1234/core.example"

    # Correct v3 endpoint and params.
    assert captured["url"].startswith("https://api.core.ac.uk/v3/search/works")
    assert captured["auth"] == "Bearer core_test_key"
    assert captured["params"]["q"] == "transformers"
    assert captured["params"]["limit"] == "10"  # min(5 * 2, 100)
    assert captured["params"]["offset"] == "0"
    # v3-unsupported params must not be sent.
    assert "year_from" not in captured["params"]
    assert "year_to" not in captured["params"]
    assert "has_fulltext" not in captured["params"]


async def test_search_core_not_found_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(core_mod, "_core_api_key", "core_test_key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/search/works" in str(request.url)
        return httpx.Response(404, text="not found")

    with _patched_async_client(handler):
        results = await search_core("transformers")

    assert results == []


async def test_search_core_empty_query_no_network(monkeypatch) -> None:
    monkeypatch.setattr(core_mod, "_core_api_key", "core_test_key")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no network call expected")

    with _patched_async_client(handler):
        results = await search_core("   ")

    assert results == []
