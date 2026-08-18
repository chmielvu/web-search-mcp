from __future__ import annotations

import json

import httpx
import pytest

from kindly_web_search_mcp_server.search.academic.academic_bn import search_bn

SAMPLE_DOCUMENTS = [
    {
        "publicationId": "1817926",
        "publicationType": "ARTICLE",
        "mainTitle": "Extreme Learning Machine (ELM) do modelowania dwuwymiarowych nieliniowości w układach napędowych",
        "publishedDate": "2015",
        "mainAbstract": "The aim of this article is to discuss extreme learning machine (ELM).",
        "contributors": ["M. Jastrzębski", "J. Kabziński", "G. Wasiak"],
        "journalTitle": "Prace Naukowe Instytutu Maszyn, Napędów i Pomiarów Elektrycznych Politechniki Wrocławskiej. Studia i Materiały",
        "issueYear": "2015",
        "fullTextFormats": ["PDF"],
    },
    {
        "publicationId": "100123",
        "publicationType": "ARTICLE",
        "mainTitle": "Drugi artykuł o uczeniu maszynowym",
        "publishedDate": "2018-06-01",
        "mainAbstract": None,
        "contributors": ["A. Autor"],
        "journalTitle": "Przegląd Elektrotechniczny",
    },
]


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Replace httpx.AsyncClient with a MockTransport-backed factory."""

    original_client = httpx.AsyncClient

    def make_client(**kwargs):
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)


async def test_search_bn_returns_two_papers(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.method == "POST"
        body = json.loads(request.content) if request.content else {}
        assert body["searchCriteria"]["generalSearchString"] == "machine learning"
        assert body["paginationCriteria"]["pageNumber"] == 1
        assert body["paginationCriteria"]["pageSize"] == 10
        assert body["paginationCriteria"]["sortingCriteria"] == {
            "fieldName": "score",
            "direction": "DESC",
        }
        return httpx.Response(
            200,
            json={"documents": SAMPLE_DOCUMENTS, "totalResults": 2},
        )

    _patch_client(monkeypatch, handler)

    papers = await search_bn("machine learning")

    assert captured, "expected an HTTP request"
    assert len(papers) == 2

    first = papers[0]
    assert first.title == SAMPLE_DOCUMENTS[0]["mainTitle"]
    assert first.authors == ["M. Jastrzębski", "J. Kabziński", "G. Wasiak"]
    assert first.abstract == SAMPLE_DOCUMENTS[0]["mainAbstract"]
    assert first.year == 2015
    assert first.venue == SAMPLE_DOCUMENTS[0]["journalTitle"]
    assert first.citations is None
    assert first.url == "https://bibliotekanauki.pl/articles/1817926"
    assert first.pdf_url is None
    assert first.source == "bn"
    assert first.source_id == "1817926"
    assert first.external_ids is None
    assert first.is_open_access is True

    second = papers[1]
    assert second.year == 2018
    assert second.abstract is None
    assert second.url == "https://bibliotekanauki.pl/articles/100123"


async def test_search_bn_clamps_page_size_to_50(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"documents": [], "totalResults": 0})

    _patch_client(monkeypatch, handler)

    papers = await search_bn("machine learning", limit=30)

    assert papers == []
    body = json.loads(captured[0].content)
    assert body["paginationCriteria"]["pageSize"] == 50


async def test_search_bn_error_returns_empty(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    _patch_client(monkeypatch, handler)

    papers = await search_bn("machine learning")

    assert papers == []


async def test_search_bn_skips_documents_without_publication_id(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "documents": [
                    {**SAMPLE_DOCUMENTS[0], "publicationId": None},
                    SAMPLE_DOCUMENTS[1],
                ],
                "totalResults": 2,
            },
        )

    _patch_client(monkeypatch, handler)

    papers = await search_bn("machine learning")

    assert len(papers) == 1
    assert papers[0].source_id == "100123"
