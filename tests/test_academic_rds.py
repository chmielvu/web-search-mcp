"""Tests for the RDS Dataverse academic provider (httpx.MockTransport)."""

from __future__ import annotations

import httpx

from kindly_web_search_mcp_server.search.academic.academic_rds import search_rds

_RDS_PAYLOAD = {
    "status": "OK",
    "data": {
        "total_count": 2,
        "items": [
            {
                "name": "Dane wyborcze 1991",
                "type": "dataset",
                "url": "https://doi.org/10.18150/ABC123",
                "citationHtml": "<p>Kowalski, Jan (1992)</p>",
                "description": "<p>Dane dotyczące <b>wyborów</b> parlamentarnych.</p>",
            },
            {
                "name": "Badanie opinii 2005",
                "type": "dataset",
                "url": "https://doi.org/10.18150/DEF456",
                "citationHtml": "<p>Nowak, Anna (2006)</p>",
                "description": "Prosty opis bez HTML.",
            },
        ],
    },
}


async def test_search_rds_returns_papers() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/api/search")
        assert request.url.params["q"] == "test"
        assert request.url.params["type"] == "dataset"
        assert request.url.params["per_page"] == "4"  # min(2*2, 50)
        return httpx.Response(200, json=_RDS_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_rds("test", limit=2, http_client=client)

    assert len(papers) == 2
    first = papers[0]
    assert first.title == "Dane wyborcze 1991"
    assert first.authors == []
    assert first.abstract == "Dane dotyczące wyborów parlamentarnych."  # HTML stripped
    assert first.year is None
    assert first.venue == "RDS Dataverse"
    assert first.citations is None
    assert first.url == "https://doi.org/10.18150/ABC123"
    assert first.source == "rds"
    assert first.source_id == "https://doi.org/10.18150/ABC123"
    assert first.is_open_access is True

    second = papers[1]
    assert second.title == "Badanie opinii 2005"
    assert second.abstract == "Prosty opis bez HTML."


async def test_search_rds_returns_empty_on_500() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_rds("test", limit=2, http_client=client)

    assert papers == []


async def test_search_rds_per_page_capped_at_50() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["per_page"] = request.url.params["per_page"]
        return httpx.Response(
            200, json={"status": "OK", "data": {"total_count": 0, "items": []}}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await search_rds("test", limit=50, http_client=client)

    assert seen["per_page"] == "50"
