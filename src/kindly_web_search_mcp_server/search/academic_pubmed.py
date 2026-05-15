"""PubMed API provider for academic search.

Uses NCBI E-utilities API (no Python SDK needed):
- 35M+ biomedical citations from MEDLINE
- Two-step search: esearch.fcgi for IDs, efetch.fcgi for metadata
- Clinical trials, case reports, biomedical literature
- Optional API key for higher rate limits (10 RPS vs 3 RPS)

PubMed is best for:
- Biomedical and clinical literature
- MEDLINE database
- Clinical trials, case reports
"""

from __future__ import annotations

import asyncio
import logging
import os
import xml.etree.ElementTree as ET

import httpx

from ..models import AcademicPaper

logger = logging.getLogger(__name__)

PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

_pubmed_api_key = os.environ.get("PUBMED_API_KEY", "")


def _build_pubmed_query(query: str, year_from: int | None, year_to: int | None) -> str:
    """Build PubMed query with date filter."""
    if year_from and year_to:
        return f"{query} AND {year_from}:{year_to}[dp]"
    elif year_from:
        return f"{query} AND {year_from}[dp]"
    elif year_to:
        return f"{query} AND :{year_to}[dp]"
    return query


def _extract_abstract(article: ET.Element) -> str | None:
    """Extract abstract from PubMed article XML."""
    abstract_elem = article.find(".//Abstract")
    if not abstract_elem:
        return None

    abstract_texts = []
    for text_elem in abstract_elem.findall(".//AbstractText"):
        label = text_elem.get("Label", "")
        text = "".join(text_elem.itertext()).strip()
        if text:
            if label:
                abstract_texts.append(f"{label}: {text}")
            else:
                abstract_texts.append(text)

    return " ".join(abstract_texts) if abstract_texts else None


def _parse_pubmed_article(article: ET.Element) -> AcademicPaper | None:
    """Parse PubMed XML article to AcademicPaper."""
    pmid_elem = article.find(".//PMID")
    if not pmid_elem or not pmid_elem.text:
        return None
    pmid = pmid_elem.text

    title_elem = article.find(".//ArticleTitle")
    title = "".join(title_elem.itertext()).strip() if title_elem else ""
    if not title:
        return None

    authors = []
    for author in article.findall(".//Author"):
        fore_name = author.find("ForeName")
        last_name = author.find("LastName")
        if fore_name and fore_name.text and last_name and last_name.text:
            authors.append(f"{fore_name.text} {last_name.text}")
        elif last_name and last_name.text:
            authors.append(last_name.text)

    year_elem = article.find(".//PubDate/Year")
    year = int(year_elem.text) if year_elem and year_elem.text else None

    venue_elem = article.find(".//Journal/Title")
    venue = "".join(venue_elem.itertext()).strip() if venue_elem else None

    doi_elem = article.find(".//ArticleId[@IdType='doi']")
    doi = doi_elem.text if doi_elem else None

    abstract = _extract_abstract(article)

    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

    external_ids = {"PubMed": pmid}
    if doi:
        external_ids["DOI"] = doi

    return AcademicPaper(
        title=title,
        authors=authors,
        abstract=abstract,
        year=year,
        venue=venue,
        citations=None,  # PubMed doesn't provide citation counts
        url=url,
        pdf_url=None,  # PubMed doesn't have direct PDF links
        source="pubmed",
        source_id=pmid,
        external_ids=external_ids,
        fields_of_study=["Medicine"],  # PubMed is biomedical-focused
        is_open_access=None,  # PubMed doesn't indicate OA status
    )


async def search_pubmed(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    fields_of_study: list[str] | None = None,  # PubMed is biomedical-only
    open_access_only: bool = False,  # Not supported by PubMed
) -> list[AcademicPaper]:
    """Search PubMed via NCBI E-utilities API.

    Two-step process:
    1. esearch.fcgi - search for PubMed IDs
    2. efetch.fcgi - fetch metadata for those IDs

    Returns empty list on error (fail gracefully).
    """
    if not query.strip():
        return []

    pubmed_query = _build_pubmed_query(query, year_from, year_to)

    async with httpx.AsyncClient(timeout=30) as client:
        # Step 1: Search for IDs
        search_params = {
            "db": "pubmed",
            "term": pubmed_query,
            "retmax": min(limit * 2, 100),
            "retmode": "xml",
        }
        if _pubmed_api_key:
            search_params["api_key"] = _pubmed_api_key

        try:
            search_resp = await client.get(PUBMED_SEARCH_URL, params=search_params)
            search_resp.raise_for_status()
        except Exception as e:
            logger.warning(f"PubMed search failed: {e}")
            return []

        try:
            search_root = ET.fromstring(search_resp.content)
        except ET.ParseError as e:
            logger.warning(f"PubMed XML parse error: {e}")
            return []

        ids = [id_elem.text for id_elem in search_root.findall(".//Id") if id_elem.text]
        if not ids:
            return []

        # Step 2: Fetch metadata
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "xml",
        }
        if _pubmed_api_key:
            fetch_params["api_key"] = _pubmed_api_key

        try:
            fetch_resp = await client.get(PUBMED_FETCH_URL, params=fetch_params, timeout=60)
            fetch_resp.raise_for_status()
        except Exception as e:
            logger.warning(f"PubMed fetch failed: {e}")
            return []

        try:
            fetch_root = ET.fromstring(fetch_resp.content)
        except ET.ParseError as e:
            logger.warning(f"PubMed fetch XML parse error: {e}")
            return []

        papers: list[AcademicPaper] = []
        for article in fetch_root.findall(".//PubmedArticle"):
            paper = _parse_pubmed_article(article)
            if paper is not None:
                papers.append(paper)
            if len(papers) >= limit:
                break

        return papers