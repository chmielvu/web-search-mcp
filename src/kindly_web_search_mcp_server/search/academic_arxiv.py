"""ArXiv API provider for academic search.

Uses the arxiv Python SDK (pip install arxiv):
- 2.5M+ CS/Physics/Math preprints
- Lucene query syntax with field prefixes (ti, au, cat, abs)
- Category taxonomy (cs.AI, stat.ML, etc.)
- Built-in pagination, retry, and polite delay (3s)
- No authentication required
"""

from __future__ import annotations

import logging
from typing import Any

import arxiv

from ..models import AcademicPaper
from ..retry import retry_with_backoff

logger = logging.getLogger(__name__)

ARXIV_CATEGORY_MAP: dict[str, list[str]] = {
    "Computer Science": ["cs"],
    "Physics": [
        "physics",
        "astro-ph",
        "cond-mat",
        "gr-qc",
        "hep-ex",
        "hep-lat",
        "hep-ph",
        "hep-th",
        "math-ph",
        "nlin",
        "nucl-ex",
        "nucl-th",
        "quant-ph",
    ],
    "Mathematics": ["math"],
    "Statistics": ["stat"],
    "Economics": ["econ"],
    "Quantitative Biology": ["q-bio"],
    "Quantitative Finance": ["q-fin"],
    "Electrical Engineering and Systems Science": ["eess"],
}


def _build_arxiv_query(
    query: str,
    fields_of_study: list[str] | None,
    year_from: int | None,
    year_to: int | None,
) -> str:
    parts: list[str] = [f'all:"{query}"']
    if fields_of_study:
        cat_parts = []
        for fos in fields_of_study:
            prefixes = ARXIV_CATEGORY_MAP.get(fos, [])
            if prefixes:
                for prefix in prefixes[:3]:
                    cat_parts.append(f"cat:{prefix}*")
            else:
                cat_parts.append(f"cat:{fos}")
        if cat_parts:
            parts.append("(" + " OR ".join(cat_parts) + ")")
    if year_from or year_to:
        start = f"{year_from}01010000" if year_from else "000001010000"
        end = f"{year_to}12312359" if year_to else "999912312359"
        parts.append(f"submittedDate:[{start} TO {end}]")
    if len(parts) == 1:
        return parts[0]
    return " AND ".join(parts)


def _normalize_result(result: arxiv.Result) -> AcademicPaper:
    arxiv_id = result.get_short_id()
    arxiv_id_clean = (
        arxiv_id.rsplit("v", 1)[0]
        if "v" in arxiv_id and arxiv_id[-1].isdigit()
        else arxiv_id
    )

    authors_list = [a.name for a in result.authors] if result.authors else []

    abstract = (result.summary or "").strip()
    abstract = " ".join(abstract.split()) if abstract else None

    year = None
    if result.published:
        try:
            year = result.published.year
        except (AttributeError, TypeError):
            pass

    primary_category = (
        result.primary_category if hasattr(result, "primary_category") else None
    )

    pdf_url = result.pdf_url or f"https://arxiv.org/pdf/{arxiv_id_clean}"
    url = result.entry_id or f"https://arxiv.org/abs/{arxiv_id_clean}"

    doi = result.doi if hasattr(result, "doi") and result.doi else None

    external_ids: dict[str, str] = {"ArXiv": arxiv_id_clean}
    if doi:
        external_ids["DOI"] = doi

    return AcademicPaper(
        title=result.title.strip() if result.title else "Untitled",
        authors=authors_list,
        abstract=abstract,
        year=year,
        venue=primary_category,
        citations=None,
        url=url,
        pdf_url=pdf_url,
        source="arxiv",
        source_id=arxiv_id_clean,
        external_ids=external_ids,
        fields_of_study=None,
        is_open_access=True,
    )


async def search_arxiv(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    fields_of_study: list[str] | None = None,
) -> list[AcademicPaper]:
    """Search ArXiv using the arxiv SDK and return normalized AcademicPaper results.

    The SDK handles Atom XML parsing, pagination, and polite delays.
    We run the sync SDK in a thread to avoid blocking the event loop.
    """
    if not query.strip():
        return []

    import asyncio

    arxiv_query = _build_arxiv_query(query, fields_of_study, year_from, year_to)
    max_results = min(limit * 2, 50)

    sort_by = arxiv.SortCriterion.Relevance

    def _sync_search() -> list[AcademicPaper]:
        client = arxiv.Client(
            page_size=max_results,
            delay_seconds=3.0,
            num_retries=3,
        )
        search = arxiv.Search(
            query=arxiv_query,
            max_results=max_results,
            sort_by=sort_by,
        )
        papers: list[AcademicPaper] = []
        try:
            for result in client.results(search):
                paper = _normalize_result(result)
                papers.append(paper)
                if len(papers) >= limit:
                    break
        except Exception as e:
            logger.warning(f"ArXiv SDK search failed: {e}")
        return papers

    return await retry_with_backoff(
        lambda: asyncio.to_thread(_sync_search),
        provider_name="arxiv",
        max_retries=2,
        initial_delay_ms=3000,
    )
