"""CrossRef API provider for academic search.

Uses the habanero Python SDK (pip install habanero):
- DOI-based metadata lookup and citation counts
- Works API for searching papers
- is-referenced-by-count for citation counts
- Reference lists, bibliographic data (publisher, type, ISSN)

CrossRef is best for:
- DOI-based metadata enrichment
- Citation counts
- Reference lists
- Publisher/journal metadata
"""

from __future__ import annotations

import asyncio
import logging
import os

from habanero import Crossref

from ..models import AcademicPaper
from ..retry import retry_with_backoff

logger = logging.getLogger(__name__)

# Polite pool configuration
_crossref_mailto = os.environ.get("CROSSREF_MAILTO", "")


def _normalize_crossref(item: dict) -> AcademicPaper | None:
    """Normalize CrossRef work to AcademicPaper."""
    title = item.get("title", [])
    if not title:
        return None

    title_str = title[0] if isinstance(title, list) else str(title)
    if not title_str.strip():
        return None

    authors = []
    for a in item.get("author", []) or []:
        given = a.get("given", "")
        family = a.get("family", "")
        if given or family:
            authors.append(f"{given} {family}".strip())

    # CrossRef doesn't provide abstracts
    abstract = None

    year = None
    published = (
        item.get("published")
        or item.get("published-print")
        or item.get("published-online")
    )
    if published:
        date_parts = published.get("date-parts", [])
        if date_parts and date_parts[0]:
            try:
                year = int(date_parts[0][0])
            except (ValueError, TypeError):
                year = None

    venue = None
    container = item.get("container-title", [])
    if container:
        venue = container[0] if isinstance(container, list) else str(container)

    citations = item.get("is-referenced-by-count", 0)
    if citations is not None:
        try:
            citations = int(citations)
        except (ValueError, TypeError):
            citations = 0

    doi = item.get("DOI", "")
    url = item.get("URL") or f"https://doi.org/{doi}" if doi else None
    if not url:
        return None  # Need at least a URL

    # CrossRef doesn't have PDF URLs
    pdf_url = None

    # External IDs - DOI is primary
    external_ids = {"DOI": doi} if doi else None

    return AcademicPaper(
        title=title_str.strip(),
        authors=authors,
        abstract=abstract,
        year=year,
        venue=venue,
        citations=citations,
        url=url,
        pdf_url=pdf_url,
        source="crossref",
        source_id=doi,
        external_ids=external_ids,
        fields_of_study=None,  # CrossRef doesn't provide fields of study
        is_open_access=None,  # CrossRef doesn't indicate OA status
    )


async def search_crossref(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    fields_of_study: list[str] | None = None,  # Not used by CrossRef
    open_access_only: bool = False,  # Not used by CrossRef
) -> list[AcademicPaper]:
    """Search CrossRef via habanero library.

    habanero is a sync library, so we wrap it in asyncio.to_thread.
    CrossRef doesn't support field_of_study or open_access filters.
    Returns empty list on error (fail gracefully).
    """
    if not query.strip():
        return []

    cr = Crossref(mailto=_crossref_mailto)

    filters = {}
    if year_from:
        filters["from-pub-date"] = str(year_from)
    if year_to:
        filters["until-pub-date"] = str(year_to)

    def _sync_search() -> list[AcademicPaper]:
        try:
            result = cr.works(
                query=query,
                limit=min(limit * 2, 100),
                filter=filters if filters else None,
            )

            items = result.get("message", {}).get("items", [])
            papers: list[AcademicPaper] = []
            for item in items:
                paper = _normalize_crossref(item)
                if paper is not None:
                    papers.append(paper)
                if len(papers) >= limit:
                    break
            return papers
        except Exception as e:
            logger.warning(f"CrossRef search failed: {e}")
            return []

    return await retry_with_backoff(
        lambda: asyncio.to_thread(_sync_search),
        provider_name="crossref",
        max_retries=1,  # CrossRef is generally reliable
        initial_delay_ms=2000,
    )
