from __future__ import annotations

import json

import httpx
import pytest

from kindly_web_search_mcp_server.search.academic.academic_pbn import search_pbn

SAMPLE_ITEMS = [
    {
        "objectId": "6123456789",
        "title": "Uczenie maszynowe w analizie danych biomedycznych",
        "year": 2021,
        "doi": "10.2478/umcs-2021-0012",
        "type": "ART",
        "authors": [
            {"firstName": "Anna", "lastName": "Nowicka"},
            {"firstName": "Piotr", "lastName": "Wiśniewski"},
        ],
        "journalTitle": "Annales UMCS. Informatica",
    },
    {
        "objectId": "6123456790",
        "title": {"pl": "Drugi tytuł po polsku", "en": "Second title in English"},
        "year": "2022",
        "doi": None,
        "authors": [{"name": "Jan", "lastName": "Kowalski"}],
        "venue": "Automatyka",
    },
]


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Replace httpx.AsyncClient with a MockTransport-backed factory."""

    original_client = httpx.AsyncClient

    def make_client(**kwargs):
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PBN_APP_ID", "pbn-test-id")
    monkeypatch.setenv("PBN_APP_TOKEN", "pbn-test-token")


async def test_search_pbn_without_env_returns_empty_without_network(monkeypatch) -> None:
    monkeypatch.delenv("PBN_APP_ID", raising=False)
    monkeypatch.delenv("PBN_APP_TOKEN", raising=False)
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"publications": []})

    _patch_client(monkeypatch, handler)

    papers = await search_pbn("machine learning")

    assert papers == []
    assert calls == [], "PBN must not touch the network when credentials are missing"


async def test_search_pbn_returns_papers_with_headers(monkeypatch) -> None:
    _set_env(monkeypatch)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.method == "POST"
        assert request.headers.get("X-App-Id") == "pbn-test-id"
        assert request.headers.get("X-App-Token") == "pbn-test-token"
        body = json.loads(request.content) if request.content else {}
        assert body == {"title": "machine learning", "page": 0, "size": 10}
        return httpx.Response(200, json={"publications": SAMPLE_ITEMS})

    _patch_client(monkeypatch, handler)

    papers = await search_pbn("machine learning")

    assert captured, "expected an HTTP request"
    assert len(papers) == 2

    first = papers[0]
    assert first.title == SAMPLE_ITEMS[0]["title"]
    assert first.authors == ["Anna Nowicka", "Piotr Wiśniewski"]
    assert first.year == 2021
    assert first.venue == "Annales UMCS. Informatica"
    assert first.citations is None
    assert first.url == "https://pbn.nauka.gov.pl/publication/6123456789"
    assert first.source == "pbn"
    assert first.source_id == "6123456789"
    assert first.external_ids == {"DOI": "10.2478/umcs-2021-0012"}

    second = papers[1]
    assert second.title == "Drugi tytuł po polsku"  # prefers "pl"
    assert second.year == 2022
    assert second.venue == "Automatyka"
    assert second.external_ids is None


async def test_search_pbn_403_returns_empty(monkeypatch) -> None:
    _set_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": 403, "message": "Forbidden"})

    _patch_client(monkeypatch, handler)

    papers = await search_pbn("machine learning")

    assert papers == []


async def test_search_pbn_handles_content_wrapper(monkeypatch) -> None:
    _set_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": {"items": SAMPLE_ITEMS}})

    _patch_client(monkeypatch, handler)

    papers = await search_pbn("machine learning")

    assert len(papers) == 2


async def test_search_pbn_handles_plain_list_response(monkeypatch) -> None:
    _set_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE_ITEMS)

    _patch_client(monkeypatch, handler)

    papers = await search_pbn("machine learning")

    assert len(papers) == 2
    assert papers[0].source_id == "6123456789"


async def test_search_pbn_skips_items_without_object_id(monkeypatch) -> None:
    _set_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"publications": [{**SAMPLE_ITEMS[0], "objectId": None}]},
        )

    _patch_client(monkeypatch, handler)

    papers = await search_pbn("machine learning")

    assert papers == []
