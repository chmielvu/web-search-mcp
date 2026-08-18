"""Tests for the Semantic Scholar academic search provider."""

from __future__ import annotations

from contextlib import contextmanager
from unittest import mock

import httpx

from kindly_web_search_mcp_server.search.academic.academic_s2 import (
    _build_year_param,
    _normalize_paper,
    search_semanticscholar,
)

S2_FULL_PAPER = {
    "paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
    "title": "Attention Is All You Need",
    "abstract": "The dominant sequence transduction models are based on complex recurrent "
    "or convolutional neural networks.",
    "year": 2017,
    "authors": [
        {"authorId": "a1", "name": "Ashish Vaswani"},
        {"authorId": "a2", "name": "Noam Shazeer"},
    ],
    "citationCount": 120000,
    "venue": "NeurIPS",
    "url": "https://www.semanticscholar.org/paper/204e3073870fae3d05bcbc2f6a8e263d9b72e776",
    "externalIds": {
        "DBLP": "conf/nips/VaswaniSPUJGKP17",
        "DOI": "10.48550/arXiv.1706.03762",
    },
    "fieldsOfStudy": ["Computer Science"],
    "isOpenAccess": True,
    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
}


@contextmanager
def _patched_async_client(handler) -> mock._patch:
    """Patch the provider's httpx.AsyncClient with a MockTransport-backed one."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    with mock.patch(
        "kindly_web_search_mcp_server.search.academic.academic_s2.httpx.AsyncClient",
        return_value=client,
    ):
        yield client


def test_build_year_param() -> None:
    assert _build_year_param(2017, 2023) == "2017-2023"
    assert _build_year_param(2017, None) == "2017-"
    assert _build_year_param(None, 2023) == "-2023"
    assert _build_year_param(None, None) is None


def test_normalize_paper() -> None:
    paper = _normalize_paper(S2_FULL_PAPER)
    assert paper is not None
    assert paper.title == "Attention Is All You Need"
    assert paper.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert paper.citations == 120000
    assert paper.external_ids == {
        "DBLP": "conf/nips/VaswaniSPUJGKP17",
        "DOI": "10.48550/arXiv.1706.03762",
    }
    assert paper.year == 2017
    assert paper.venue == "NeurIPS"
    assert paper.pdf_url == "https://arxiv.org/pdf/1706.03762"
    assert paper.is_open_access is True
    assert paper.fields_of_study == ["Computer Science"]

    assert _normalize_paper({}) is None
    assert _normalize_paper({"title": "   "}) is None


async def test_search_semanticscholar_parses_paper(monkeypatch) -> None:
    monkeypatch.setenv("S2_API_KEY", "s2_test_key")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url).startswith(
            "https://api.semanticscholar.org/graph/v1/paper/search"
        )
        params = dict(request.url.params)
        captured["params"] = params
        assert params["query"] == "transformers"
        assert params["limit"] == "10"  # min(5 * 2, 100)
        assert params["year"] == "2020-2024"
        assert params["venue"] == "NeurIPS,ICLR"
        assert params["fieldsOfStudy"] == "Computer Science"
        assert params["openAccessPdf"] == "true"
        assert "title" in params["fields"]
        assert request.headers.get("x-api-key") == "s2_test_key"
        return httpx.Response(200, json={"total": 1, "offset": 0, "data": [S2_FULL_PAPER]})

    with _patched_async_client(handler):
        results = await search_semanticscholar(
            "transformers",
            limit=5,
            year_from=2020,
            year_to=2024,
            fields_of_study=["Computer Science"],
            venue="NeurIPS,ICLR",
            open_access_only=True,
        )

    assert len(results) == 1
    paper = results[0]
    assert paper.title == "Attention Is All You Need"
    assert paper.citations == 120000
    assert paper.external_ids["DBLP"] == "conf/nips/VaswaniSPUJGKP17"
    assert paper.source == "semanticscholar"
    assert paper.pdf_url == "https://arxiv.org/pdf/1706.03762"
    assert captured["params"]["year"] == "2020-2024"


async def test_search_semanticscholar_rate_limited_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("S2_API_KEY", raising=False)
    request_seen = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_seen
        request_seen = True
        return httpx.Response(429, text="rate limited")

    with _patched_async_client(handler):
        results = await search_semanticscholar("transformers", limit=5)

    assert request_seen
    assert results == []


async def test_search_semanticscholar_timeout_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("S2_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    with _patched_async_client(handler):
        results = await search_semanticscholar("transformers", limit=5)

    assert results == []


async def test_search_semanticscholar_http_error_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("S2_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with _patched_async_client(handler):
        results = await search_semanticscholar("transformers", limit=5)

    assert results == []


async def test_search_semanticscholar_empty_query_no_network(monkeypatch) -> None:
    monkeypatch.delenv("S2_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no network call expected")

    with _patched_async_client(handler):
        results = await search_semanticscholar("   ", limit=5)

    assert results == []
