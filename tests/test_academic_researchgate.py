"""Unit tests for the ResearchGate DOI-alias provider."""

from __future__ import annotations
import httpx
from typing import Any

import pytest

from kindly_web_search_mcp_server.search.academic.academic_researchgate import (
    search_researchgate,
)


def _mock_openalex_response(*works: dict[str, Any]) -> dict[str, Any]:
    return {"results": list(works), "meta": {"count": len(works)}}


def _work(
    *,
    title: str = "A Study",
    doi: str = "10.1234/example",
    authors: list[str] | None = None,
    year: int | None = 2023,
    venue: str | None = "Journal of Examples",
    citations: int | None = 12,
    is_oa: bool = True,
    oa_url: str | None = "https://example.com/oa.pdf",
    abstract_text: str | None = "A study about things.",
) -> dict[str, Any]:
    work: dict[str, Any] = {
        "title": title,
        "ids": {"doi": f"https://doi.org/{doi}"} if doi else None,
        "publication_year": year,
        "cited_by_count": citations,
        "open_access": {"is_oa": is_oa, "oa_url": oa_url},
        "primary_location": {
            "source": {"display_name": venue} if venue else None,
        },
        "authorships": [
            {"author": {"display_name": name}} for name in (authors or [])
        ],
        "abstract_inverted_index": (
            {w: [i] for i, w in enumerate(abstract_text.split())}
            if abstract_text
            else None
        ),
    }
    return work


def _make_transport(handler):
    return httpx.MockTransport(handler)


async def _call(query: str, *, work: dict[str, Any]) -> list:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mock_openalex_response(work))

    transport = _make_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        return await search_researchgate(query, limit=3, http_client=client)


@pytest.mark.asyncio
async def test_search_researchgate_basic_paper() -> None:
    work = _work(
        title="  DOI-Alias Paper  ",
        doi="10.1/alias",
        authors=["Ada Lovelace", "Grace Hopper"],
        year=2024,
        venue="OpenAlex Quarterly",
        citations=42,
    )
    papers = await _call("DOI-Alias Paper", work=work)
    assert len(papers) == 1
    p = papers[0]
    assert p.title == "DOI-Alias Paper"
    assert p.source == "researchgate"
    assert p.source_id == "10.1/alias"
    assert p.source_type == "general"
    assert p.external_ids == {"DOI": "10.1/alias"}
    assert p.url == "https://www.researchgate.net/publication/10.1/alias"
    assert p.citations == 42
    assert p.venue == "OpenAlex Quarterly"
    assert p.authors == ["Ada Lovelace", "Grace Hopper"]


@pytest.mark.asyncio
async def test_search_researchgate_skips_no_doi() -> None:
    work = _work(title="No DOI", doi="")
    papers = await _call("anything", work=work)
    assert papers == []


@pytest.mark.asyncio
async def test_search_researchgate_strips_doi_prefix() -> None:
    work = _work(title="x", doi="10.5/foo")
    papers = await _call("x", work=work)
    assert papers[0].url.endswith("/10.5/foo")
    assert papers[0].source_id == "10.5/foo"


@pytest.mark.asyncio
async def test_search_researchgate_empty_query_no_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network should not be called for empty query")

    transport = _make_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_researchgate("", limit=3, http_client=client)
    assert papers == []


@pytest.mark.asyncio
async def test_search_researchgate_500_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = _make_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_researchgate("anything", limit=3, http_client=client)
    assert papers == []


@pytest.mark.asyncio
async def test_search_researchgate_year_filter_in_params() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_mock_openalex_response())

    transport = _make_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await search_researchgate("x", limit=3, year_from=2020, year_to=2023, http_client=client)
    assert "filter" in seen
    assert "from_publication_date:2020-01-01" in seen["filter"]
    assert "to_publication_date:2023-12-31" in seen["filter"]


@pytest.mark.asyncio
async def test_search_researchgate_caps_at_limit() -> None:
    works = [_work(title=f"Paper {i}", doi=f"10.1/{i}") for i in range(5)]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mock_openalex_response(*works))

    transport = _make_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_researchgate("x", limit=2, http_client=client)
    assert len(papers) == 2


@pytest.mark.asyncio
async def test_search_researchgate_falls_back_to_legacy_abstract_field() -> None:
    work = _work(title="Legacy abstract", doi="10.1/legacy")
    work["abstract_inverted_index"] = None
    work["abstract"] = "Legacy plain abstract."
    papers = await _call("x", work=work)
    assert papers[0].abstract == "Legacy plain abstract."


@pytest.mark.asyncio
async def test_search_researchgate_secondary_location_venue() -> None:
    work = _work(title="No primary venue", doi="10.1/secondary")
    work["primary_location"] = {"source": None}
    work["locations"] = [
        {"source": None},
        {"source": {"display_name": "Secondary Journal"}},
    ]
    papers = await _call("x", work=work)
    assert papers[0].venue == "Secondary Journal"


@pytest.mark.asyncio
async def test_search_researchgate_is_oa_bool_only() -> None:
    work = _work(title="OA unknown", doi="10.1/oa", is_oa=False)
    papers = await _call("x", work=work)
    assert papers[0].is_open_access is False

    work2 = _work(title="OA missing", doi="10.1/oa2")
    work2["open_access"] = None
    papers2 = await _call("x", work=work2)
    assert papers2[0].is_open_access is None


@pytest.mark.asyncio
async def test_search_researchgate_passes_query_and_per_page() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_mock_openalex_response())

    transport = _make_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await search_researchgate("graph neural networks", limit=4, http_client=client)
    assert seen["search"] == "graph neural networks"
