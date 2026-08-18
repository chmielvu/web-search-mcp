"""RAD-on (POL-on) opendata API provider for academic search.

Queries the Polish national research registry (RAD-on, radon.nauka.gov.pl)
open-data publications API:

- No API key required
- GET https://radon.nauka.gov.pl/opendata/polon/publications
- ``title`` performs a fragment match against publication titles

RAD-on is best for:
- Polish scientific publications (articles, books, chapters, proceedings)
- National registry metadata (type, journal, DOI, open-access flag)
"""

from __future__ import annotations

import logging

import httpx

from ...models import AcademicPaper

logger = logging.getLogger(__name__)

_RADON_API_URL = "https://radon.nauka.gov.pl/opendata/polon/publications"
_TIMEOUT = httpx.Timeout(20.0)


def _extract_abstract(item: dict) -> str | None:
    """Return the first usable abstract from the ``abstracts`` list."""
    abstracts = item.get("abstracts") or []
    if not isinstance(abstracts, list) or not abstracts:
        return None
    first = abstracts[0]
    if isinstance(first, str):
        text = first.strip()
    elif isinstance(first, dict):
        text = str(first.get("content") or first.get("text") or "").strip()
    else:
        text = str(first).strip()
    return text or None


def _normalize_radon(item: dict) -> AcademicPaper | None:
    """Normalize a RAD-on publication to AcademicPaper."""
    title = item.get("title")
    if not title or not str(title).strip():
        return None
    title_str = str(title).strip()

    source_id = item.get("objectId")
    if not source_id:
        return None
    source_id = str(source_id).strip()

    authors: list[str] = []
    for author in item.get("authors") or []:
        if not isinstance(author, dict):
            continue
        given = author.get("firstName") or author.get("name") or ""
        family = author.get("lastName") or ""
        name = f"{given} {family}".strip()
        if name:
            authors.append(name)

    year = item.get("year")
    if isinstance(year, str) and year.strip().isdigit():
        year = int(year.strip())
    if not isinstance(year, int):
        year = None

    venue: str | None = None
    journal = item.get("journal")
    if isinstance(journal, dict) and journal.get("title"):
        venue = str(journal["title"]).strip() or None
    if not venue and item.get("publisher"):
        venue = str(item["publisher"]).strip() or None

    doi = item.get("doi")
    if isinstance(doi, str) and doi.strip():
        doi = doi.strip()
    else:
        doi = None

    url = item.get("publicUri") or _RADON_API_URL
    if url:
        url = str(url).strip()
    if not url:
        return None

    open_access = item.get("openAccess")
    is_open_access = open_access if isinstance(open_access, bool) else None

    return AcademicPaper(
        title=title_str,
        authors=authors,
        abstract=_extract_abstract(item),
        year=year,
        venue=venue,
        citations=None,
        url=url,
        pdf_url=None,
        source="radon",
        source_type="polish",
        source_id=source_id,
        external_ids={"DOI": doi} if doi else None,
        fields_of_study=None,
        is_open_access=is_open_access,
        score=None,
    )


async def search_radon(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[AcademicPaper]:
    """Search RAD-on opendata for Polish publications; never raises.

    ``year_from``/``year_to`` are accepted for interface parity with the other
    academic providers; the RAD-on opendata API does not expose a verified
    year filter, so they are currently ignored.
    """
    result_numbers = max(1, min(limit * 2, 100))
    params = {"resultNumbers": result_numbers, "title": query}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_RADON_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - providers must never raise
        logger.warning("RAD-on search failed: %s", exc)
        return []

    items = data.get("results") if isinstance(data, dict) else None
    papers: list[AcademicPaper] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        paper = _normalize_radon(item)
        if paper is not None:
            papers.append(paper)
    return papers
