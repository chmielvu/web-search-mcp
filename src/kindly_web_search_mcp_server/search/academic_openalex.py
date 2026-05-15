"""OpenAlex API provider for academic search.

Uses the pyalex Python SDK (pip install pyalex):
- 250M+ works, 16 entity types (works, authors, institutions...)
- Free tier: polite pool with email config, faster with API key
- Rich metadata: abstracts, citations, topics, open access
- Supports year/field filters, open access filter

Implementation pattern from paper-search-mcp (1,451 stars).
"""

from __future__ import annotations

import asyncio
import logging
import os

import pyalex

from ..models import AcademicPaper
from ..retry import retry_with_backoff

logger = logging.getLogger(__name__)

# Configure pyalex (polite pool or with API key)
_openalex_email = os.environ.get("KINDLY_OPENALEX_EMAIL", "")
_openalex_api_key = os.environ.get("KINDLY_OPENALEX_API_KEY", "")
pyalex.config.email = _openalex_email
pyalex.config.api_key = _openalex_api_key


def _extract_external_ids(work: dict) -> dict[str, str] | None:
    """Extract DOI, ArXiv, PubMed IDs from OpenAlex work."""
    ids = work.get("ids", {})
    ext = {}
    if ids.get("doi"):
        doi = ids["doi"]
        if doi.startswith("https://doi.org/"):
            doi = doi.replace("https://doi.org/", "")
        ext["DOI"] = doi
    if ids.get("pmid"):
        ext["PubMed"] = ids["pmid"]
    if ids.get("arxiv"):
        arxiv_id = ids["arxiv"]
        if arxiv_id.startswith("https://arxiv.org/abs/"):
            arxiv_id = arxiv_id.replace("https://arxiv.org/abs/", "")
        ext["ArXiv"] = arxiv_id
    return ext if ext else None


def _normalize_openalex(work: dict) -> AcademicPaper | None:
    """Normalize OpenAlex work to AcademicPaper."""
    if not work.get("title"):
        return None

    authors = []
    for a in work.get("authorships", []) or []:
        author_info = a.get("author", {})
        name = author_info.get("display_name")
        if name:
            authors.append(name)

    abstract = work.get("abstract") or None

    year = work.get("publication_year")
    if year is not None:
        try:
            year = int(year)
        except (ValueError, TypeError):
            year = None

    venue = None
    primary_source = work.get("primary_location") or work.get("primary_source")
    if primary_source:
        source_info = primary_source.get("source") if isinstance(primary_source, dict) else primary_source
        if isinstance(source_info, dict):
            venue = source_info.get("display_name")

    citations = work.get("cited_by_count", 0)
    if citations is not None:
        try:
            citations = int(citations)
        except (ValueError, TypeError):
            citations = 0

    work_id = work.get("id", "")
    if work_id.startswith("https://openalex.org/"):
        work_id = work_id.replace("https://openalex.org/", "")

    url = work.get("id") or f"https://openalex.org/{work_id}"

    oa_info = work.get("open_access") or {}
    pdf_url = oa_info.get("oa_url")
    is_oa = oa_info.get("is_oa", False)

    topics = work.get("topics", [])
    fos = [t.get("display_name") for t in topics if t.get("display_name")] if topics else None

    return AcademicPaper(
        title=work["title"].strip() if isinstance(work["title"], str) else str(work["title"]).strip(),
        authors=authors,
        abstract=abstract.strip() if abstract else None,
        year=year,
        venue=venue,
        citations=citations,
        url=url,
        pdf_url=pdf_url,
        source="openalex",
        source_id=work_id,
        external_ids=_extract_external_ids(work),
        fields_of_study=fos,
        is_open_access=is_oa if isinstance(is_oa, bool) else None,
    )


async def search_openalex(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    fields_of_study: list[str] | None = None,
    open_access_only: bool = False,
) -> list[AcademicPaper]:
    """Search OpenAlex via pyalex library.

    pyalex is a sync library, so we wrap it in asyncio.to_thread.
    Filters: year_from/year_to, open_access_only.
    Returns empty list on error (fail gracefully).
    """
    if not query.strip():
        return []

    filters = {}
    if year_from:
        filters["from_publication_date"] = f"{year_from}-01-01"
    if year_to:
        filters["to_publication_date"] = f"{year_to}-12-31"
    if open_access_only:
        filters["is_oa"] = True

    def _sync_search() -> list[AcademicPaper]:
        try:
            works = pyalex.Works()
            if filters:
                works = works.filter(**filters)
            results = works.search(query).get(per_page=min(limit * 2, 100))

            papers: list[AcademicPaper] = []
            for w in results:
                paper = _normalize_openalex(w)
                if paper is not None:
                    papers.append(paper)
                if len(papers) >= limit:
                    break
            return papers
        except Exception as e:
            logger.warning(f"OpenAlex search failed: {e}")
            return []

    return await retry_with_backoff(
        lambda: asyncio.to_thread(_sync_search),
        provider_name="openalex",
        max_retries=1,  # OpenAlex is generally reliable, 1 retry
        initial_delay_ms=2000,
    )