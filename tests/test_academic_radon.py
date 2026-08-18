from __future__ import annotations

import httpx
import pytest

from kindly_web_search_mcp_server.search.academic.academic_radon import (
    search_radon,
)

LIMIT_DEFAULTS = 5

SAMPLE_RESULTS = [
    {
        "objectId": "5e70f72e46e0fb0001b31f6e",
        "title": "Approximation spaces in machine learning and pattern recognition",
        "year": 2005,
        "type": "ARTICLE",
        "typeName": "Artykuł",
        "journal": {
            "objectId": "5e70f51046e0fb0001b28a4d",
            "title": "Lecture Notes in Computer Science",
            "issn": "0302-9743",
        },
        "publisher": None,
        "authors": [{"firstName": "Jarosław", "lastName": "Stepaniuk"}],
        "doi": "10.1007/11539506_8",
        "openAccess": True,
        "publicUri": "https://radon.nauka.gov.pl/opendata/polon/publications/5e70f72e46e0fb0001b31f6e",
        "keywords": [],
        "abstracts": ["A study of approximation spaces in machine learning."],
    },
    {
        "objectId": "5e70f72e46e0fb0001b31f70",
        "title": "Second publication title",
        "year": "2010",
        "type": "BOOK",
        "typeName": "Książka",
        "journal": None,
        "publisher": "Wydawnictwo Naukowe PWN",
        "authors": [
            {"name": "Maria", "lastName": "Kowalska"},
            {"name": "Jan", "lastName": "Nowak"},
        ],
        "doi": None,
        "openAccess": None,
        "publicUri": None,
        "keywords": [],
        "abstracts": [],
    },
]


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Replace httpx.AsyncClient with a MockTransport-backed factory."""

    original_client = httpx.AsyncClient

    def make_client(**kwargs):
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)


async def test_search_radon_returns_two_papers(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.method == "GET"
        assert request.url.params["title"] == "machine learning"
        assert request.url.params["resultNumbers"] == str(LIMIT_DEFAULTS * 2)
        return httpx.Response(
            200,
            json={"results": SAMPLE_RESULTS, "pagination": {"maxCount": 2, "token": "abc"}},
        )

    _patch_client(monkeypatch, handler)

    papers = await search_radon("machine learning", limit=LIMIT_DEFAULTS)

    assert captured, "expected an HTTP request"
    assert len(papers) == 2

    first = papers[0]
    assert first.title == SAMPLE_RESULTS[0]["title"]
    assert first.authors == ["Jarosław Stepaniuk"]
    assert first.year == 2005
    assert first.venue == "Lecture Notes in Computer Science"
    assert first.citations is None
    assert first.url == SAMPLE_RESULTS[0]["publicUri"]
    assert first.source == "radon"
    assert first.source_id == "5e70f72e46e0fb0001b31f6e"
    assert first.external_ids == {"DOI": "10.1007/11539506_8"}
    assert first.is_open_access is True
    assert first.abstract == "A study of approximation spaces in machine learning."

    second = papers[1]
    assert second.authors == ["Maria Kowalska", "Jan Nowak"]
    assert second.year == 2010
    assert second.venue == "Wydawnictwo Naukowe PWN"
    assert second.url == "https://radon.nauka.gov.pl/opendata/polon/publications"
    assert second.external_ids is None
    assert second.is_open_access is None
    assert second.abstract is None


async def test_search_radon_clamps_result_numbers_to_100(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": [], "pagination": {}})

    _patch_client(monkeypatch, handler)

    papers = await search_radon("machine learning", limit=70)

    assert papers == []
    assert captured[0].url.params["resultNumbers"] == "100"


async def test_search_radon_error_returns_empty(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    _patch_client(monkeypatch, handler)

    papers = await search_radon("machine learning")

    assert papers == []


async def test_search_radon_skips_items_without_title(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {**SAMPLE_RESULTS[0], "title": None},
                    {**SAMPLE_RESULTS[1], "objectId": None},
                    SAMPLE_RESULTS[0],
                ]
            },
        )

    _patch_client(monkeypatch, handler)

    papers = await search_radon("machine learning")

    assert len(papers) == 1
    assert papers[0].source_id == SAMPLE_RESULTS[0]["objectId"]
