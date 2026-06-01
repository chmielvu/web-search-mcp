"""Semantic Scholar API provider for academic search.

Uses the semanticscholar Python SDK (pip install semanticscholar):
- 214M+ papers, 2.49B+ citations
- Free tier: 1 RPS with API key, shared rate limit without
- Rich metadata: abstracts, citations, fields of study, open access
- Supports year/venue/fieldOfStudy filters

Phase 1 Fix: Switch to AsyncSemanticScholar for native async,
fail-fast with retry=False, configurable timeout.
"""

from __future__ import annotations

import logging
import os

from semanticscholar import AsyncSemanticScholar

from ..models import AcademicPaper

logger = logging.getLogger(__name__)

# Fail-fast configuration (Phase 1.2)
S2_TIMEOUT = int(os.environ.get("KINDLY_S2_TIMEOUT", "30"))
# retry=False means SDK won't retry on 429/5xx - we handle fail-fast
S2_RETRY_ENABLED = os.environ.get("KINDLY_S2_MAX_RETRIES", "0") != "0"  # False when 0


def _get_api_key() -> str | None:
    raw = (os.environ.get("KINDLY_S2_API_KEY") or "").strip()
    return raw if raw else None


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
    """Search Semantic Scholar using AsyncSemanticScholar (native async).

    Fail-fast configuration:
    - retry=False: SDK does not retry on 429/5xx
    - timeout=S2_TIMEOUT: Configurable timeout (default 30s)
    - Returns empty list on error (not raise)

    This lets the orchestrator return partial results from other providers.
    """
    if not query.strip():
        return []

    api_key = _get_api_key()
    sch = AsyncSemanticScholar(
        api_key=api_key,
        timeout=S2_TIMEOUT,
        retry=S2_RETRY_ENABLED,  # False when KINDLY_S2_MAX_RETRIES=0 (fail fast)
    )

    year_str: str | None = None
    if year_from and year_to:
        year_str = f"{year_from}-{year_to}"
    elif year_from:
        year_str = f"{year_from}-"
    elif year_to:
        year_str = f"-{year_to}"

    try:
        results = await sch.search_paper(
            query,
            year=year_str,
            fields_of_study=fields_of_study,
            venue=[venue] if venue else None,
            limit=min(limit * 2, 100),
            open_access_pdf=True if open_access_only else None,
        )

        papers: list[AcademicPaper] = []
        # AsyncSemanticScholar returns PaginatedResults - iterate items directly
        for item in results.items if hasattr(results, "items") else results:
            raw = item.__dict__ if hasattr(item, "__dict__") else dict(item)
            paper = _normalize_paper(raw)
            if paper is not None:
                papers.append(paper)
            if len(papers) >= limit:
                break
        return papers

    except Exception as e:
        msg = str(e)
        if "429" in msg or "rate" in msg.lower():
            logger.warning(
                "Semantic Scholar search rate-limited: %s. Set KINDLY_S2_API_KEY for higher limits.",
                e,
            )
        elif "timeout" in msg.lower() or "timed out" in msg.lower():
            logger.warning(
                "Semantic Scholar search timed out (configurable via KINDLY_S2_TIMEOUT): %s",
                e,
            )
        else:
            logger.warning("Semantic Scholar search failed: %s", e)
        # Return empty, not raise - let orchestrator return partial results
        return []
