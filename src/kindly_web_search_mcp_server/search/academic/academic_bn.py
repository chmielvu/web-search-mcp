"""Biblioteka Nauki (BN) provider for academic search.

Queries the Polish Library of Science open catalog:

- No API key required
- POST https://bibliotekanauki.pl/api/search with a JSON body
- Open-access platform: full texts are available for most records

BN is best for:
- Polish scientific articles and journals with full-text availability
- Open-access metadata (the platform is OA by design)
"""

from __future__ import annotations

import logging
import re

import httpx

from ...models import AcademicPaper

logger = logging.getLogger(__name__)

_BN_API_URL = "https://bibliotekanauki.pl/api/search"
_BN_ARTICLE_URL = "https://bibliotekanauki.pl/articles"
_TIMEOUT = httpx.Timeout(20.0)


def _extract_year(published_date: object) -> int | None:
    """Parse a publication year from BN's ``publishedDate`` string."""
    if published_date is None:
        return None
    text = str(published_date).strip()
    if text.isdigit():
        return int(text)
    match = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
    if match:
        return int(match.group(1))
    return None


def _normalize_bn(item: dict) -> AcademicPaper | None:
    """Normalize a Biblioteka Nauki document to AcademicPaper."""
    title = item.get("mainTitle")
    if not title or not str(title).strip():
        return None
    title_str = str(title).strip()

    publication_id = item.get("publicationId")
    if publication_id is None or str(publication_id).strip() == "":
        return None
    publication_id = str(publication_id).strip()

    contributors = item.get("contributors") or []
    authors = [str(c).strip() for c in contributors if isinstance(c, str) and c.strip()]

    abstract_raw = item.get("mainAbstract")
    abstract = str(abstract_raw).strip() if abstract_raw else None
    if not abstract:
        abstract = None

    venue_raw = item.get("journalTitle")
    venue = str(venue_raw).strip() if venue_raw else None

    return AcademicPaper(
        title=title_str,
        authors=authors,
        abstract=abstract,
        year=_extract_year(item.get("publishedDate")),
        venue=venue,
        citations=None,
        url=f"{_BN_ARTICLE_URL}/{publication_id}",
        pdf_url=None,
        source="bn",
        source_type="polish",
        source_id=publication_id,
        external_ids=None,
        fields_of_study=None,
        is_open_access=True,
        score=None,
    )


async def search_bn(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[AcademicPaper]:
    """Search Biblioteka Nauki; never raises.

    ``year_from``/``year_to`` are accepted for interface parity with the other
    academic providers; the BN search API does not expose a verified year
    filter, so they are currently ignored.
    """
    payload = {
        "searchCriteria": {"generalSearchString": query},
        "paginationCriteria": {
            "pageNumber": 1,
            "pageSize": max(1, min(limit * 2, 50)),
            "sortingCriteria": {"fieldName": "score", "direction": "DESC"},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_BN_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - providers must never raise
        logger.warning("Biblioteka Nauki search failed: %s", exc)
        return []

    documents = data.get("documents") if isinstance(data, dict) else None
    papers: list[AcademicPaper] = []
    for document in documents or []:
        if not isinstance(document, dict):
            continue
        paper = _normalize_bn(document)
        if paper is not None:
            papers.append(paper)
    return papers
