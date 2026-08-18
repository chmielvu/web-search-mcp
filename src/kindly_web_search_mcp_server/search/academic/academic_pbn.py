"""PBN (Polska Bibliografia Naukowa) provider for academic search.

Queries the PBN API (token-gated):

- Requires ``PBN_APP_ID`` and ``PBN_APP_TOKEN`` environment variables
- POST https://pbn.nauka.gov.pl/api/v1/search/publications
- Credentials are read at call time via ``os.environ``; without both the
  provider returns [] immediately without any network access

PBN is best for:
- Polish scientific bibliography with DOIs and publication types
"""

from __future__ import annotations

import logging
import os

import httpx

from ...models import AcademicPaper

logger = logging.getLogger(__name__)

_PBN_API_URL = "https://pbn.nauka.gov.pl/api/v1/search/publications"
_PBN_PUBLICATION_URL = "https://pbn.nauka.gov.pl/publication"
_TIMEOUT = httpx.Timeout(20.0)

# Keys that may wrap the publication list in the search response.
_CONTENT_KEYS = ("publications", "content", "items")
# Nested keys inside a wrapper object (e.g. {"content": {"items": [...]}}).
_NESTED_KEYS = ("items", "results", "records")


def _extract_title(value: object) -> str | None:
    """Best-effort title extraction; PBN may return localized title maps."""
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for lang in ("pl", "en"):
            candidate = value.get(lang)
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        for candidate in value.values():
            if candidate and str(candidate).strip():
                return str(candidate).strip()
    return None


def _extract_year(value: object) -> int | None:
    """Best-effort year extraction from int or numeric string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _normalize_pbn(item: dict) -> AcademicPaper | None:
    """Normalize a PBN publication to AcademicPaper."""
    title = _extract_title(item.get("title"))
    if not title:
        return None

    object_id = item.get("objectId") or item.get("publicationId") or item.get("id")
    if object_id is None or str(object_id).strip() == "":
        # AcademicPaper requires a URL; PBN pages are keyed by objectId.
        return None
    object_id = str(object_id).strip()
    url = f"{_PBN_PUBLICATION_URL}/{object_id}"

    authors: list[str] = []
    for author in item.get("authors") or []:
        if isinstance(author, str):
            name = author.strip()
        elif isinstance(author, dict):
            given = author.get("firstName") or author.get("name") or ""
            family = author.get("lastName") or ""
            name = f"{given} {family}".strip()
        else:
            continue
        if name:
            authors.append(name)

    venue_raw = item.get("journalTitle") or item.get("venue")
    venue = str(venue_raw).strip() if venue_raw else None

    doi = item.get("doi")
    if isinstance(doi, str) and doi.strip():
        doi = doi.strip()
    else:
        doi = None

    return AcademicPaper(
        title=title,
        authors=authors,
        abstract=None,
        year=_extract_year(item.get("year")),
        venue=venue,
        citations=None,
        url=url,
        pdf_url=None,
        source="pbn",
        source_type="polish",
        source_id=object_id,
        external_ids={"DOI": doi} if doi else None,
        fields_of_study=None,
        is_open_access=None,
        score=None,
    )


def _extract_items(data: object) -> list:
    """Defensively unwrap the publication list from a PBN response."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in _CONTENT_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested in _NESTED_KEYS:
                if isinstance(value.get(nested), list):
                    return value[nested]
    return []


def _pbn_credentials() -> tuple[str, str] | None:
    """Return (app_id, app_token) from the environment, if configured."""
    app_id = os.environ.get("PBN_APP_ID", "").strip()
    app_token = os.environ.get("PBN_APP_TOKEN", "").strip()
    if not app_id or not app_token:
        return None
    return app_id, app_token


async def search_pbn(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[AcademicPaper]:
    """Search PBN (token-gated); never raises.

    Credentials are read from ``PBN_APP_ID``/``PBN_APP_TOKEN`` at call time;
    without both configured the search returns [] without any network access.
    """
    creds = _pbn_credentials()
    if creds is None:
        logger.info("PBN credentials missing (PBN_APP_ID/PBN_APP_TOKEN); skipping PBN search")
        return []
    app_id, app_token = creds

    payload = {"title": query, "page": 0, "size": max(1, min(limit * 2, 100))}
    headers = {"X-App-Id": app_id, "X-App-Token": app_token}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_PBN_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - providers must never raise
        logger.warning("PBN search failed: %s", exc)
        return []

    items = _extract_items(data)
    papers: list[AcademicPaper] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        paper = _normalize_pbn(item)
        if paper is not None:
            papers.append(paper)
    return papers
