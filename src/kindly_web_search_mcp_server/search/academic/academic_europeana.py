"""Europeana provider for academic search.

Europeana aggregates digitised cultural heritage across Europe; results are
scoped to Poland via qf="COUNTRY:poland". Requires an API key.

API: GET https://api.europeana.eu/record/v2/search.json
- The key is sent as the X-Api-Key header (Europeana deprecated the wskey
  URL parameter in favor of headers). No key -> the provider is skipped.
"""

from __future__ import annotations

import logging
import os

import httpx

from ...models import AcademicPaper

logger = logging.getLogger(__name__)

EUROPEANA_SEARCH_URL = "https://api.europeana.eu/record/v2/search.json"
EUROPEANA_API_KEY_ENV = "EUROPEANA_API_KEY"


def _as_scalar(value: object) -> str | None:
    """Return a string scalar from a value that may be a list (API quirk)."""
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_europeana(item: object) -> AcademicPaper | None:
    """Normalize a Europeana search item to AcademicPaper."""
    if not isinstance(item, dict):
        return None

    title = _as_scalar(item.get("title"))
    if not title:
        return None

    guid = _as_scalar(item.get("guid"))
    if not guid:
        return None

    authors = [a for a in (_as_scalar(c) for c in (item.get("dcCreator") or [])) if a]

    year = None
    year_str = _as_scalar(item.get("year"))
    if year_str:
        try:
            year = int(year_str)
        except ValueError:
            year = None

    venue = _as_scalar(item.get("dataProvider"))
    link = _as_scalar(item.get("link"))

    return AcademicPaper(
        title=title,
        authors=authors,
        abstract=None,
        year=year,
        venue=venue,
        citations=None,
        url=guid or link or "",
        pdf_url=None,
        source="europeana",
        source_id=guid,
        external_ids=None,
        fields_of_study=None,
        is_open_access=True,
        source_type="archive",
    )


async def search_europeana(
    query: str,
    *,
    limit: int = 5,
    http_client: httpx.AsyncClient | None = None,
) -> list[AcademicPaper]:
    """Search Europeana (Poland-scoped) for cultural heritage records.

    Requires EUROPEANA_API_KEY; without it the provider logs and returns an
    empty list without any network activity. Returns an empty list on any
    error (fail gracefully, never raise).
    """
    if not query.strip():
        return []

    api_key = os.environ.get(EUROPEANA_API_KEY_ENV)
    if not api_key:
        logger.info("EUROPEANA_API_KEY not set; skipping Europeana search")
        return []

    params = {
        "query": query,
        "rows": min(limit * 2, 100),
        "qf": "COUNTRY:poland",
        "profile": "standard",
    }
    headers = {"X-Api-Key": api_key}

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.get(EUROPEANA_SEARCH_URL, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        papers: list[AcademicPaper] = []
        for item in payload.get("items") or []:
            paper = _normalize_europeana(item)
            if paper is not None:
                papers.append(paper)
        return papers
    except Exception as exc:  # Provider errors are non-fatal.
        logger.warning("Europeana search failed for query=%r: %s", query, exc)
        return []
    finally:
        if owns_client:
            await client.aclose()
