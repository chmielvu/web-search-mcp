"""Polona provider for academic search.

Polona is the digital library of the National Library of Poland
(Biblioteka Narodowa). No API key required.

API: POST https://polona.pl/api/search-service/search/simple
- URL params: query, page=0, pageSize (max 24), sort=RELEVANCE
- Requires an empty JSON body.
"""

from __future__ import annotations

import logging

import httpx

from ...models import AcademicPaper

logger = logging.getLogger(__name__)

POLONA_SEARCH_URL = "https://polona.pl/api/search-service/search/simple"
POLONA_PREVIEW_URL = "https://polona.pl/preview/{object_id}"


def _field_value(basic_fields: object, field_name: str) -> str | None:
    """Return the first value for a basicFields field name, if present."""
    if not isinstance(basic_fields, dict):
        return None
    field = basic_fields.get(field_name)
    if not isinstance(field, dict):
        return None
    values = field.get("values")
    if not isinstance(values, list) or not values:
        return None
    value = values[0]
    return str(value).strip() if value is not None else None


def _normalize_polona(hit: object) -> AcademicPaper | None:
    """Normalize a Polona search hit to AcademicPaper."""
    if not isinstance(hit, dict):
        return None
    object_id = hit.get("objectId")
    if not object_id:
        return None

    basic_fields = hit.get("basicFields") or {}
    title = _field_value(basic_fields, "title")
    if not title:
        return None

    creator = _field_value(basic_fields, "creatorForSearch")
    authors = [creator] if creator else []

    # dateDescriptive is free text like "[do 1905]" — kept verbatim, not parsed.
    date_descriptive = _field_value(basic_fields, "dateDescriptive")

    return AcademicPaper(
        title=title,
        authors=authors,
        abstract=None,
        year=None,
        venue="Polona",
        citations=None,
        url=POLONA_PREVIEW_URL.format(object_id=object_id),
        pdf_url=None,
        source="polona",
        source_id=str(object_id),
        external_ids=None,
        fields_of_study=None,
        is_open_access=True,
        source_type="archive",
        date_descriptive=date_descriptive,
    )


async def search_polona(
    query: str,
    *,
    limit: int = 5,
    http_client: httpx.AsyncClient | None = None,
) -> list[AcademicPaper]:
    """Search Polona (National Library of Poland digital library).

    Returns an empty list on any error (fail gracefully, never raise).
    """
    if not query.strip():
        return []

    params = {
        "query": query,
        "page": 0,
        "pageSize": min(limit * 2, 24),
        "sort": "RELEVANCE",
    }

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.post(POLONA_SEARCH_URL, params=params, json={})
        response.raise_for_status()
        payload = response.json()
        papers: list[AcademicPaper] = []
        for hit in payload.get("hits") or []:
            paper = _normalize_polona(hit)
            if paper is not None:
                papers.append(paper)
        return papers
    except Exception as exc:  # Provider errors are non-fatal.
        logger.warning("Polona search failed for query=%r: %s", query, exc)
        return []
    finally:
        if owns_client:
            await client.aclose()
