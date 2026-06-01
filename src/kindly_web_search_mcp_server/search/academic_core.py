"""CORE API provider for academic search.

Uses CORE API v3 (https://api.core.ac.uk/v3):
- Aggregates open access papers from repositories worldwide
- Full-text availability
- Repository metadata
- 200M+ open access works

CORE is best for:
- Open access full-text search
- Repository metadata
- Papers not indexed elsewhere
"""

from __future__ import annotations

import logging
import os

import httpx

from ..models import AcademicPaper

logger = logging.getLogger(__name__)

CORE_API_URL = "https://api.core.ac.uk/v3"

_core_api_key = os.environ.get("CORE_API_KEY", "")


def _normalize_core(item: dict) -> AcademicPaper | None:
    """Normalize CORE work to AcademicPaper."""
    title = item.get("title")
    if not title or not isinstance(title, str) or not title.strip():
        return None

    authors = []
    for a in item.get("authors", []) or []:
        if isinstance(a, str):
            authors.append(a)
        elif isinstance(a, dict):
            name = a.get("name")
            if name:
                authors.append(name)

    abstract = item.get("abstract") or None
    if abstract and isinstance(abstract, str):
        abstract = abstract.strip()

    year = None
    year_published = item.get("yearPublished")
    if year_published is not None:
        try:
            year = int(year_published)
        except (ValueError, TypeError):
            year = None

    venue = None
    # CORE has various publisher/repository fields
    publisher = item.get("publisher")
    if publisher:
        venue = publisher

    # CORE doesn't have citation counts
    citations = None

    core_id = item.get("id", "")

    # URL construction
    url = item.get("downloadUrl") or item.get("url")
    if not url and core_id:
        url = f"https://api.core.ac.uk/v3/data/articles/{core_id}"

    # PDF URL - CORE specializes in full-text
    pdf_url = item.get("downloadUrl")
    has_fulltext = item.get("hasFullText", False)

    # External IDs - DOI if available
    doi = item.get("doi")
    external_ids = {}
    if doi:
        if doi.startswith("https://doi.org/"):
            doi = doi.replace("https://doi.org/", "")
        external_ids["DOI"] = doi
    if core_id:
        external_ids["CORE"] = str(core_id)
    external_ids = external_ids if external_ids else None

    return AcademicPaper(
        title=title.strip(),
        authors=authors,
        abstract=abstract,
        year=year,
        venue=venue,
        citations=citations,
        url=url or f"https://core.ac.uk/download/{core_id}",
        pdf_url=pdf_url,
        source="core",
        source_id=str(core_id),
        external_ids=external_ids,
        fields_of_study=None,  # CORE doesn't provide fields
        is_open_access=has_fulltext if isinstance(has_fulltext, bool) else True,
    )


async def search_core(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    fields_of_study: list[str] | None = None,  # Not supported by CORE
    open_access_only: bool = True,  # CORE specializes in OA, default True
) -> list[AcademicPaper]:
    """Search CORE for open access papers.

    CORE API requires API key for full access.
    Returns empty list on error (fail gracefully).
    """
    if not query.strip():
        return []

    if not _core_api_key:
        logger.info("CORE API key not configured, returning empty results")
        return []

    headers = {"Authorization": f"Bearer {_core_api_key}"}
    params = {
        "q": query,
        "limit": min(limit * 2, 100),
    }
    if year_from:
        params["year_from"] = year_from
    if year_to:
        params["year_to"] = year_to
    if open_access_only:
        params["has_fulltext"] = "true"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                f"{CORE_API_URL}/search",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("CORE rate limited")
            else:
                logger.warning(f"CORE search failed: {e}")
            return []
        except Exception as e:
            logger.warning(f"CORE search failed: {e}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"CORE JSON parse error: {e}")
            return []

        results = data.get("results", []) or data.get("data", []) or []
        papers: list[AcademicPaper] = []
        for item in results:
            paper = _normalize_core(item)
            if paper is not None:
                papers.append(paper)
            if len(papers) >= limit:
                break

        return papers
