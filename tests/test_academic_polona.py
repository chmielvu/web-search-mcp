"""Tests for the Polona academic provider (httpx.MockTransport, no network)."""

from __future__ import annotations

import httpx

from kindly_web_search_mcp_server.search.academic.academic_polona import search_polona


def _polona_hit(object_id: str, title: str, creator: str | None = None) -> dict:
    basic_fields: dict[str, dict[str, list[str]]] = {"title": {"values": [title]}}
    if creator:
        basic_fields["creatorForSearch"] = {"values": [creator]}
    return {
        "objectId": object_id,
        "basicFields": basic_fields,
        "attributes": {"thumbnail": {"stringValue": "https://thumb.example/x.jpg"}},
    }


_POLONA_PAYLOAD = {
    "totalElements": 2,
    "hits": [
        _polona_hit("obj-1", "Pierwsza książka", "Jan Kowalski"),
        _polona_hit("obj-2", "Druga książka"),
    ],
}


async def test_search_polona_returns_papers() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/search/simple")
        assert request.url.params["query"] == "test"
        assert request.url.params["page"] == "0"
        assert request.url.params["sort"] == "RELEVANCE"
        assert request.url.params["pageSize"] == "4"  # min(2*2, 24)
        assert request.content == b"{}"
        return httpx.Response(200, json=_POLONA_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_polona("test", limit=2, http_client=client)

    assert len(papers) == 2
    first = papers[0]
    assert first.title == "Pierwsza książka"
    assert first.authors == ["Jan Kowalski"]
    assert first.year is None
    assert first.venue == "Polona"
    assert first.citations is None
    assert first.url == "https://polona.pl/preview/obj-1"
    assert first.pdf_url is None
    assert first.source == "polona"
    assert first.source_id == "obj-1"
    assert first.external_ids is None
    assert first.fields_of_study is None
    assert first.is_open_access is True

    second = papers[1]
    assert second.title == "Druga książka"
    assert second.authors == []


async def test_search_polona_returns_empty_on_500() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_polona("test", limit=2, http_client=client)

    assert papers == []


async def test_search_polona_page_size_capped_at_24() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["pageSize"] = request.url.params["pageSize"]
        return httpx.Response(200, json={"totalElements": 0, "hits": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await search_polona("test", limit=50, http_client=client)

    assert seen["pageSize"] == "24"


async def test_search_polona_blank_query_returns_empty() -> None:
    assert await search_polona("   ") == []
