"""Tests for the dLibra OAI-PMH academic provider (httpx.MockTransport)."""

from __future__ import annotations

import httpx

from kindly_web_search_mcp_server.search.academic.academic_dlibra import PRESETS, search_dlibra

_OAI_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"
         xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <responseDate>2026-01-01T00:00:00Z</responseDate>
  <ListRecords>
    <record>
      <header>
        <identifier>oai:www.wbc.poznan.pl:12345</identifier>
        <datestamp>2026-01-01</datestamp>
      </header>
      <metadata>
        <oai_dc:dc>
          <dc:title>Rękopis pierwszy</dc:title>
          <dc:creator>Adam Mickiewicz</dc:creator>
          <dc:creator>Juliusz Słowacki</dc:creator>
          <dc:date>1834</dc:date>
          <dc:description>Rękopis poetycki znajdujący się w zbiorach.</dc:description>
          <dc:identifier>https://www.wbc.poznan.pl/dlibra/publication/12345</dc:identifier>
          <dc:publisher>Biblioteka Poznańska</dc:publisher>
        </oai_dc:dc>
      </metadata>
    </record>
    <record>
      <header>
        <identifier>oai:www.wbc.poznan.pl:67890</identifier>
        <datestamp>2026-01-02</datestamp>
      </header>
      <metadata>
        <oai_dc:dc>
          <dc:title>Druga pozycja</dc:title>
          <dc:date>prawdopodobnie 1905</dc:date>
          <dc:identifier>https://www.wbc.poznan.pl/dlibra/publication/67890</dc:identifier>
        </oai_dc:dc>
      </metadata>
    </record>
    <record>
      <header>
        <identifier>oai:www.wbc.poznan.pl:11111</identifier>
        <datestamp>2026-01-03</datestamp>
      </header>
      <metadata>
        <oai_dc:dc>
          <dc:title>Pozycja bez odnośnika</dc:title>
          <dc:date>1899</dc:date>
          <dc:identifier>oai:www.wbc.poznan.pl:11111</dc:identifier>
        </oai_dc:dc>
      </metadata>
    </record>
    <resumptionToken>abc123</resumptionToken>
  </ListRecords>
</OAI-PMH>
"""

_ERROR_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-01-01T00:00:00Z</responseDate>
  <error code="noRecordsMatch">No matching records</error>
</OAI-PMH>
"""


async def test_search_dlibra_returns_papers() -> None:
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert request.url.params["verb"] == "ListRecords"
        assert request.url.params["metadataPrefix"] == "oai_dc"
        assert "from" not in request.url.params
        assert "until" not in request.url.params
        return httpx.Response(200, text=_OAI_RESPONSE, headers={"Content-Type": "text/xml"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_dlibra("test", limit=5, http_client=client)

    assert request_count == 1  # resumptionToken present but not followed
    assert len(papers) == 2

    first = papers[0]
    assert first.title == "Rękopis pierwszy"
    assert first.authors == ["Adam Mickiewicz", "Juliusz Słowacki"]
    assert first.year == 1834
    assert first.venue == "Biblioteka Poznańska"
    assert first.abstract == "Rękopis poetycki znajdujący się w zbiorach."
    assert first.citations is None
    assert first.url == "https://www.wbc.poznan.pl/dlibra/publication/12345"
    assert first.source == "dlibra"
    assert first.source_id == "oai:www.wbc.poznan.pl:12345"
    assert first.is_open_access is True

    second = papers[1]
    assert second.title == "Druga pozycja"
    assert second.authors == []
    assert second.year == 1905  # first 4-digit year found in free-text dc:date
    assert second.venue is None
    assert second.url == "https://www.wbc.poznan.pl/dlibra/publication/67890"
    assert all(p.title != "Pozycja bez odnośnika" for p in papers)  # no http id -> skipped


async def test_search_dlibra_year_filters_added_to_params() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["from"] = request.url.params["from"]
        seen["until"] = request.url.params["until"]
        return httpx.Response(200, text=_OAI_RESPONSE, headers={"Content-Type": "text/xml"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await search_dlibra(
            "test", limit=2, year_from=1900, year_to=1920, http_client=client
        )

    assert seen["from"] == "1900-01-01"
    assert seen["until"] == "1920-12-31"


async def test_search_dlibra_returns_empty_on_error_xml() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_ERROR_RESPONSE, headers={"Content-Type": "text/xml"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_dlibra("test", limit=5, http_client=client)

    assert papers == []


async def test_search_dlibra_custom_base_url_and_limit_cap() -> None:
    seen_url = ""

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(200, text=_OAI_RESPONSE, headers={"Content-Type": "text/xml"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        papers = await search_dlibra("test", limit=1, http_client=client)

    assert seen_url.startswith(PRESETS["wbc"])
    assert len(papers) == 1  # capped at limit
