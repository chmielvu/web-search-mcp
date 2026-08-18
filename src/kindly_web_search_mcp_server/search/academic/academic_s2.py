"""Semantic Scholar API provider for academic search.

Uses the Semantic Scholar Graph API directly via httpx (no Python SDK):
- 214M+ papers, 2.49B+ citations
- Free tier: 1 RPS with API key, shared rate limit without
- Rich metadata: abstracts, citations, fields of study, open access
- Supports year/venue/fieldsOfStudy filters

The previous ``semanticscholar`` SDK was removed: in 0.12.0
``PaginatedResults.items`` is a method (not a property) so iteration raised
TypeError, and ``Paper.__dict__`` exposes underscore-prefixed attrs
(``_title``, ...) which dropped every paper. Direct HTTP avoids both issues and
makes fail-fast behavior explicit: 429s and timeouts return ``[]`` without the
SDK's 10x exponential-backoff retry storm.
"""

from __future__ import annotations

import logging
import os

import httpx

from ...models import AcademicPaper

logger = logging.getLogger(__name__)

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

# Configurable timeout (default 30s), read at module load.
S2_TIMEOUT = int(os.environ.get("S2_TIMEOUT", "30"))

# Fields requested from the Graph API — kept aligned with _normalize_paper.
S2_FIELDS = (
    "title,abstract,year,authors,citationCount,venue,url,"
    "externalIds,fieldsOfStudy,isOpenAccess,openAccessPdf"
)


def _get_api_key() -> str | None:
    raw = (os.environ.get("S2_API_KEY") or "").strip()
    return raw if raw else None


def _build_year_param(year_from: int | None, year_to: int | None) -> str | None:
    """Build the S2 ``year`` filter: "YYYY-YYYY", "YYYY-", "-YYYY", or None."""
    if year_from and year_to:
        return f"{year_from}-{year_to}"
    if year_from:
        return f"{year_from}-"
    if year_to:
        return f"-{year_to}"
    return None


def _normalize_paper(raw: dict) -> AcademicPaper | None:
    title = raw.get("title")
    if not title or not isinstance(title, str) or not title.strip():
        return None

    paper_id = raw.get("paperId", "")
    authors = []
    for a in raw.get("authors") or []:
        name = a.get("name")
        if name:
            authors.append(name)

    abstract = raw.get("abstract")
    if abstract and not isinstance(abstract, str):
        abstract = None

    year = raw.get("year")
    if year is not None:
        try:
            year = int(year)
        except (ValueError, TypeError):
            year = None

    citations = raw.get("citationCount")
    if citations is not None:
        try:
            citations = int(citations)
        except (ValueError, TypeError):
            citations = None

    venue = raw.get("venue") or None
    oa_pdf = raw.get("openAccessPdf")
    pdf_url = oa_pdf.get("url") if isinstance(oa_pdf, dict) else None

    url = raw.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}"

    ext_ids = raw.get("externalIds") or {}
    clean_ids = {k: str(v) for k, v in ext_ids.items() if v}

    fos = raw.get("fieldsOfStudy")
    if isinstance(fos, list):
        fos = [f for f in fos if isinstance(f, str)]
    else:
        fos = None

    is_oa = raw.get("isOpenAccess")

    return AcademicPaper(
        title=title.strip(),
        authors=authors,
        abstract=abstract.strip() if abstract else None,
        year=year,
        venue=venue,
        citations=citations,
        url=url,
        pdf_url=pdf_url,
        source="semanticscholar",
        source_id=paper_id,
        external_ids=clean_ids or None,
        fields_of_study=fos,
        is_open_access=is_oa if isinstance(is_oa, bool) else None,
    )


async def search_semanticscholar(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    fields_of_study: list[str] | None = None,
    venue: str | None = None,
    open_access_only: bool = False,
) -> list[AcademicPaper]:
    """Search Semantic Scholar Graph API via httpx.

    Fail-fast behavior:
    - 429 -> log a "rate limited" warning and return ``[]``
    - timeout / HTTP / network / parse errors -> log and return ``[]``
    - never raises; lets the orchestrator return partial results

    The limit is over-fetched (``limit * 2``, capped at 100) then trimmed.
    """
    if not query.strip():
        return []

    params: dict[str, str | int] = {
        "query": query,
        "limit": min(limit * 2, 100),
        "fields": S2_FIELDS,
    }

    year_str = _build_year_param(year_from, year_to)
    if year_str is not None:
        params["year"] = year_str
    if venue:
        # Graph API accepts a comma-joined list of venue names.
        params["venue"] = venue
    if fields_of_study:
        params["fieldsOfStudy"] = ",".join(fields_of_study)
    if open_access_only:
        params["openAccessPdf"] = "true"

    headers: dict[str, str] = {}
    api_key = _get_api_key()
    if api_key:
        headers["x-api-key"] = api_key

    try:
        async with httpx.AsyncClient(timeout=S2_TIMEOUT) as client:
            resp = await client.get(S2_SEARCH_URL, params=params, headers=headers)
            if resp.status_code == 429:
                logger.warning(
                    "Semantic Scholar search rate limited (429); "
                    "set S2_API_KEY for higher limits"
                )
                return []
            resp.raise_for_status()
            data = resp.json().get("data", [])
    except httpx.TimeoutException as e:
        logger.warning(
            "Semantic Scholar search timed out (configurable via S2_TIMEOUT): %s", e
        )
        return []
    except Exception as e:
        logger.warning("Semantic Scholar search failed: %s", e)
        return []

    papers: list[AcademicPaper] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        paper = _normalize_paper(item)
        if paper is not None:
            papers.append(paper)
        if len(papers) >= limit:
            break

    return papers
