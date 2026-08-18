"""Tests for the PubMed academic search provider."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from contextlib import contextmanager
from unittest import mock

import httpx

from kindly_web_search_mcp_server.search.academic.academic_pubmed import (
    _extract_abstract,
    _parse_pubmed_article,
    search_pubmed,
)

# Minimal realistic fixtures. Note: elements like <PMID>, <Title> and <Year>
# are childless, which is exactly what made the old `if not elem` checks drop
# every article on Python 3.12+.
ESEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eSearchResult>
  <Count>1</Count>
  <IdList>
    <Id>12345678</Id>
  </IdList>
</eSearchResult>
"""

PUBMED_ARTICLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345678</PMID>
      <Article>
        <Journal>
          <JournalIssue>
            <PubDate>
              <Year>2021</Year>
            </PubDate>
          </JournalIssue>
          <Title>Nature</Title>
        </Journal>
        <ArticleTitle>Test article title</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Background text</AbstractText>
          <AbstractText>Body text</AbstractText>
        </Abstract>
        <AuthorList>
          <Author>
            <ForeName>Jane</ForeName>
            <LastName>Roe</LastName>
          </Author>
          <Author>
            <LastName>Mystery</LastName>
          </Author>
        </AuthorList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">12345678</ArticleId>
        <ArticleId IdType="doi">10.1000/example</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


@contextmanager
def _patched_async_client(handler) -> mock._patch:
    """Patch the provider's httpx.AsyncClient with a MockTransport-backed one."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    with mock.patch(
        "kindly_web_search_mcp_server.search.academic.academic_pubmed.httpx.AsyncClient",
        return_value=client,
    ):
        yield client


def test_parse_pubmed_article() -> None:
    root = ET.fromstring(PUBMED_ARTICLE_XML)
    article = root.find(".//PubmedArticle")
    assert article is not None

    paper = _parse_pubmed_article(article)

    assert paper is not None
    assert paper.title == "Test article title"
    assert paper.authors == ["Jane Roe", "Mystery"]
    assert paper.year == 2021
    assert paper.venue == "Nature"
    assert paper.abstract == "BACKGROUND: Background text Body text"
    assert paper.url == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert paper.source == "pubmed"
    assert paper.source_id == "12345678"
    assert paper.external_ids == {"PubMed": "12345678", "DOI": "10.1000/example"}


def test_parse_pubmed_article_missing_core_fields_returns_none() -> None:
    no_pmid = ET.fromstring("<PubmedArticle><Article/></PubmedArticle>")
    assert _parse_pubmed_article(no_pmid) is None

    no_title = ET.fromstring(
        "<PubmedArticle><MedlineCitation><PMID>1</PMID>"
        "<Article><ArticleTitle></ArticleTitle></Article>"
        "</MedlineCitation></PubmedArticle>"
    )
    assert _parse_pubmed_article(no_title) is None


def test_extract_abstract_labeled_text() -> None:
    article = ET.fromstring(
        "<Article><Abstract>"
        '<AbstractText Label="BACKGROUND">First sentence.</AbstractText>'
        "<AbstractText>Second sentence.</AbstractText>"
        "</Abstract></Article>"
    )
    assert _extract_abstract(article) == "BACKGROUND: First sentence. Second sentence."


def test_extract_abstract_missing() -> None:
    assert _extract_abstract(ET.fromstring("<Article/>")) is None
    # Empty <Abstract/> element exists but has no AbstractText -> None.
    assert _extract_abstract(ET.fromstring("<Article><Abstract/></Article>")) is None


async def test_search_pubmed_end_to_end() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "esearch.fcgi" in url:
            return httpx.Response(200, content=ESEARCH_XML.encode("utf-8"))
        if "efetch.fcgi" in url:
            return httpx.Response(200, content=PUBMED_ARTICLE_XML.encode("utf-8"))
        raise AssertionError(f"unexpected request URL: {url}")

    with _patched_async_client(handler):
        results = await search_pubmed("test query", limit=5)

    assert len(results) == 1
    paper = results[0]
    assert paper.title == "Test article title"
    assert paper.authors == ["Jane Roe", "Mystery"]
    assert paper.year == 2021
    assert paper.venue == "Nature"
    assert paper.external_ids == {"PubMed": "12345678", "DOI": "10.1000/example"}


async def test_search_pubmed_empty_ids_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "esearch.fcgi" in str(request.url)
        return httpx.Response(
            200,
            content=b'<eSearchResult><Count>0</Count><IdList></IdList></eSearchResult>',
        )

    with _patched_async_client(handler):
        results = await search_pubmed("nothing", limit=5)

    assert results == []


async def test_search_pubmed_error_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with _patched_async_client(handler):
        results = await search_pubmed("test", limit=5)

    assert results == []
