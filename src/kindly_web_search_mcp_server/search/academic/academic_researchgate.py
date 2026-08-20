"""ResearchGate DOI-alias provider for academic search.

ResearchGate has no official public API and is gated by anti-bot protections.
This provider implements the DOI-alias approach from the overhaul plan:
queries the OpenAlex Works API (which indexes most ResearchGate-hosted papers
by their DOIs) and rewrites each result URL to the ResearchGate landing page
``https://www.researchgate.net/publication/{doi}``. The paper's metadata
(title, authors, year, venue, citations, abstract, OA) all come from OpenAlex;
only the URL is redirected to ResearchGate.

No API key is required; OpenAlex is more cooperative without a key than with
a ResearchGate account and provides far richer metadata.
"""

from __future__ import annotations

import logging

import httpx

from ...models import AcademicPaper

logger = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
_RESEARCHGATE_URL_TEMPLATE = "https://www.researchgate.net/publication/{doi}"
_TIMEOUT = httpx.Timeout(20.0)


def _normalize_researchgate(work: dict) -> AcademicPaper | None:
    """Normalize an OpenAlex work into a ResearchGate-aliased AcademicPaper."""
    if not isinstance(work, dict):
        return None
    title = work.get("title")
    if not title or not isinstance(title, str) or not title.strip():
        return None

    # Extract DOI from the OpenAlex ids block.
    ids = work.get("ids", {}) or {}
    doi_raw = ids.get("doi") if isinstance(ids, dict) else None
    if not doi_raw or not isinstance(doi_raw, str):
        return None
    doi = doi_raw.replace("https://doi.org/", "").strip()
    if not doi:
        return None

    # Authors
    authors: list[str] = []
    for authorship in work.get("authorships") or []:
        author = (authorship or {}).get("author") if isinstance(authorship, dict) else None
        if isinstance(author, dict):
            name = author.get("display_name")
            if name:
                authors.append(name)

    # Year
    year = work.get("publication_year")
    if year is not None:
        try:
            year = int(year)
        except (ValueError, TypeError):
            year = None

    # Venue: prefer the journal source, fall back to the primary location.
    primary = work.get("primary_location") or {}
    source_info = primary.get("source") if isinstance(primary, dict) else None
    venue: str | None = None
    if isinstance(source_info, dict):
        venue = source_info.get("display_name")
    if not venue:
        for loc in work.get("locations") or []:
            if not isinstance(loc, dict):
                continue
            src = loc.get("source")
            if isinstance(src, dict) and src.get("display_name"):
                venue = src["display_name"]
                break

    # Abstract (may be missing or inverted-indexed in OpenAlex).
    abstract_raw = work.get("abstract_inverted_index")
    abstract: str | None = None
    if isinstance(abstract_raw, dict) and abstract_raw:
        try:
            abstract = " ".join(
                _abstract_word_positions(abstract_raw)
            ).strip() or None
        except Exception:
            abstract = None
    if abstract is None:
        # Older OpenAlex records expose a plain "abstract" field.
        plain = work.get("abstract")
        if isinstance(plain, str) and plain.strip():
            abstract = plain.strip()

    # Citations + open access.
    citations = work.get("cited_by_count")
    if citations is not None:
        try:
            citations = int(citations)
        except (ValueError, TypeError):
            citations = None
    oa = work.get("open_access") or {}
    is_oa = oa.get("is_oa") if isinstance(oa, dict) else None
    pdf_url = oa.get("oa_url") if isinstance(oa, dict) else None

    return AcademicPaper(
        title=title.strip(),
        authors=authors,
        abstract=abstract,
        year=year,
        venue=venue,
        citations=citations,
        url=_RESEARCHGATE_URL_TEMPLATE.format(doi=doi),
        pdf_url=pdf_url,
        source="researchgate",
        source_id=doi,
        external_ids={"DOI": doi},
        fields_of_study=None,
        is_open_access=is_oa if isinstance(is_oa, bool) else None,
        source_type="general",
    )


def _abstract_word_positions(inverted_index: dict) -> list[str]:
    """Reconstruct a plain-text abstract from OpenAlex's inverted index.

    OpenAlex stores abstracts as ``{word: [positions]}`; reassemble the words
    sorted by their first position to get a readable sentence.
    """
    items = []
    for word, positions in inverted_index.items():
        if not positions:
            continue
        try:
            first_pos = min(int(p) for p in positions)
        except (ValueError, TypeError):
            continue
        items.append((first_pos, word))
    items.sort(key=lambda x: x[0])
    return [word for _, word in items]


async def search_researchgate(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[AcademicPaper]:
    """Search OpenAlex and return ResearchGate-aliased papers.

    Returns an empty list on any error (fail gracefully, never raise).
    """
    if not query.strip():
        return []

    params: dict[str, str | int] = {
        "search": query,
        "per_page": min(limit * 2, 100),
    }
    filters: dict[str, str] = {}
    if year_from:
        filters["from_publication_date"] = f"{year_from}-01-01"
    if year_to:
        filters["to_publication_date"] = f"{year_to}-12-31"
    if filters:
        params["filter"] = ",".join(f"{k}:{v}" for k, v in filters.items())

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        response = await client.get(OPENALEX_WORKS_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("ResearchGate (OpenAlex DOI-alias) search failed: %s", exc)
        return []
    finally:
        if owns_client:
            await client.aclose()

    results = payload.get("results") or []
    papers: list[AcademicPaper] = []
    for work in results:
        paper = _normalize_researchgate(work)
        if paper is not None:
            papers.append(paper)
        if len(papers) >= limit:
            break
    return papers
