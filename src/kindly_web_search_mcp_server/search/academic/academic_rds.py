"""RDS Dataverse provider for academic search.

RDS (Repozytorium Danych Społecznych) is the Polish social science data
archive, running Dataverse software. No API key required.

API: GET https://rds.icm.edu.pl/api/search
- URL params: q, type=dataset, per_page (max 50)
"""

from __future__ import annotations

import logging
import re

import httpx

from ...models import AcademicPaper

logger = logging.getLogger(__name__)

RDS_SEARCH_URL = "https://rds.icm.edu.pl/api/search"

_HTML_TAG_RE = re.compile(r"<[^>]*>")


def _strip_html(text: str | None) -> str | None:
    """Remove HTML tags from a string with a simple regex."""
    if not text:
        return None
    cleaned = _HTML_TAG_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _normalize_rds(item: object) -> AcademicPaper | None:
    """Normalize an RDS Dataverse search item to AcademicPaper."""
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    url = item.get("url")
    if not isinstance(name, str) or not name.strip() or not isinstance(url, str) or not url:
        return None

    return AcademicPaper(
        title=name.strip(),
        authors=[],
        abstract=_strip_html(item.get("description")),
        year=None,
        venue="RDS Dataverse",
        citations=None,
        url=url,
        pdf_url=None,
        source="rds",
        source_id=url,
        external_ids=None,
        fields_of_study=None,
        is_open_access=True,
        source_type="archive",
    )


async def search_rds(
    query: str,
    *,
    limit: int = 5,
    http_client: httpx.AsyncClient | None = None,
) -> list[AcademicPaper]:
    """Search the RDS Dataverse archive (Polish social science data).

    Returns an empty list on any error (fail gracefully, never raise).
    """
    if not query.strip():
        return []

    params = {
        "q": query,
        "type": "dataset",
        "per_page": min(limit * 2, 50),
    }

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.get(RDS_SEARCH_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        papers: list[AcademicPaper] = []
        for item in data.get("items") or []:
            paper = _normalize_rds(item)
            if paper is not None:
                papers.append(paper)
        return papers
    except Exception as exc:  # Provider errors are non-fatal.
        logger.warning("RDS search failed for query=%r: %s", query, exc)
        return []
    finally:
        if owns_client:
            await client.aclose()
