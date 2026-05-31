"""Academic search orchestrator: parallel provider search → dedup → merge → response.

Follows the same pattern as search/orchestrator.py (web search):
1. Resolve which providers fire (default: arxiv + semanticscholar)
2. Fire providers in parallel via asyncio.gather
3. Normalize results to AcademicPaper
4. Deduplicate by DOI > ArXiv ID > title fuzzy match
5. Sort (relevance default, or citation count)
6. Return AcademicSearchResponse with per-provider warnings

Phase 2-5: Added OpenAlex, CrossRef, PubMed, CORE providers.
"""

from __future__ import annotations

import asyncio
import logging

from ..models import AcademicPaper, AcademicSearchResponse, ProviderWarning
from ..utils.observability import emit_observability_event
from .academic_arxiv import search_arxiv
from .academic_core import search_core
from .academic_crossref import search_crossref
from .academic_openalex import search_openalex
from .academic_pubmed import search_pubmed
from .academic_s2 import search_semanticscholar

logger = logging.getLogger(__name__)


def _dedup_keys(paper: AcademicPaper) -> set[str]:
    """Generate all possible dedup keys for a paper: DOI, ArXiv ID, title.

    A paper with both DOI and ArXiv ID generates keys for both,
    enabling cross-source matching even when one provider has partial IDs.
    """
    keys: set[str] = set()
    ext = paper.external_ids or {}
    doi = ext.get("DOI")
    if doi:
        keys.add(f"doi:{doi.lower()}")
    arxiv = ext.get("ArXiv")
    if arxiv:
        keys.add(f"arxiv:{arxiv.lower()}")
    pmid = ext.get("PubMed")
    if pmid:
        keys.add(f"pmid:{pmid.lower()}")
    core = ext.get("CORE")
    if core:
        keys.add(f"core:{core.lower()}")
    # Always add title as fallback (lowercased, stripped)
    keys.add(f"title:{paper.title.lower().strip()}")
    return keys


def _merge_papers(
    result_lists: list[list[AcademicPaper]],
) -> list[AcademicPaper]:
    """Deduplicate papers across providers, keeping richer metadata.

    A paper matches another if ANY of its dedup keys overlaps:
    DOI, ArXiv ID, PubMed ID, CORE ID, or title. When two papers match,
    prefer the one with more metadata (abstract, citations).
    """
    # Map from each key to the canonical paper index
    key_to_idx: dict[str, int] = {}
    papers_in_order: list[AcademicPaper] = []

    for papers in result_lists:
        for paper in papers:
            keys = _dedup_keys(paper)
            # Check if any key already maps to an existing paper
            existing_idx: int | None = None
            for key in keys:
                if key in key_to_idx:
                    existing_idx = key_to_idx[key]
                    break

            if existing_idx is not None:
                # Merge with existing paper, keeping richer metadata
                existing = papers_in_order[existing_idx]
                merged = _merge_two(existing, paper)
                papers_in_order[existing_idx] = merged
                # Add ALL keys for this paper to the index
                for key in keys:
                    key_to_idx[key] = existing_idx
            else:
                # New paper
                idx = len(papers_in_order)
                papers_in_order.append(paper)
                for key in keys:
                    key_to_idx[key] = idx

    return papers_in_order


def _metadata_richness(paper: AcademicPaper) -> int:
    """Score how much metadata a paper has (higher = richer)."""
    score = 0
    if paper.abstract:
        score += 2
    if paper.citations is not None:
        score += 2
    if paper.venue:
        score += 1
    if paper.fields_of_study:
        score += 1
    if paper.pdf_url:
        score += 1
    if len(paper.authors) > 0:
        score += 1
    return score


def _merge_two(existing: AcademicPaper, incoming: AcademicPaper) -> AcademicPaper:
    """Merge two papers that refer to the same work, keeping the best fields."""
    return AcademicPaper(
        title=existing.title
        if len(existing.title) >= len(incoming.title)
        else incoming.title,
        authors=existing.authors
        if len(existing.authors) >= len(incoming.authors)
        else incoming.authors,
        abstract=existing.abstract or incoming.abstract,
        year=existing.year if existing.year is not None else incoming.year,
        venue=existing.venue or incoming.venue,
        citations=existing.citations
        if existing.citations is not None
        else incoming.citations,
        url=existing.url,
        pdf_url=existing.pdf_url or incoming.pdf_url,
        source=existing.source,
        source_id=existing.source_id,
        external_ids=_merge_dicts(existing.external_ids, incoming.external_ids),
        fields_of_study=existing.fields_of_study or incoming.fields_of_study,
        is_open_access=existing.is_open_access
        if existing.is_open_access is not None
        else incoming.is_open_access,
        score=max(existing.score or 0, incoming.score or 0)
        if existing.score or incoming.score
        else None,
    )


def _merge_dicts(
    a: dict[str, str] | None, b: dict[str, str] | None
) -> dict[str, str] | None:
    if not a and not b:
        return None
    merged = {}
    if a:
        merged.update(a)
    if b:
        merged.update(b)
    return merged or None


def _sort_papers(papers: list[AcademicPaper], sort: str) -> list[AcademicPaper]:
    """Sort papers by relevance (default) or citation count."""
    if sort == "citations":
        return sorted(papers, key=lambda p: p.citations or 0, reverse=True)
    if sort == "date":
        return sorted(papers, key=lambda p: p.year or 0, reverse=True)
    return papers


def _resolve_sources(sources: list[str] | None) -> list[str]:
    """Resolve which sources to query. Default: arxiv + semanticscholar."""
    available = {"semanticscholar", "arxiv", "openalex", "crossref", "pubmed", "core"}
    if sources is None:
        return ["arxiv", "semanticscholar"]  # Default: both free-ish providers

    requested = {
        s.lower().replace("-", "").replace("_", "").replace(" ", "") for s in sources
    }
    normalized = set()
    for r in requested:
        if r in ("semanticscholar", "s2", "semantic"):
            normalized.add("semanticscholar")
        elif r in ("arxiv",):
            normalized.add("arxiv")
        elif r in ("openalex", "alex", "oa"):
            normalized.add("openalex")
        elif r in ("crossref", "cr", "doi"):
            normalized.add("crossref")
        elif r in ("pubmed", "pm", "medline"):
            normalized.add("pubmed")
        elif r in ("core",):
            normalized.add("core")
        else:
            # Unknown source, ignore
            pass
    return [s for s in normalized if s in available] or ["arxiv", "semanticscholar"]


async def run_academic_search(
    query: str,
    *,
    limit: int = 5,
    sources: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    fields_of_study: list[str] | None = None,
    venue: str | None = None,
    open_access_only: bool = False,
    sort: str = "relevance",
) -> AcademicSearchResponse:
    """Execute academic search across all providers in parallel, merge, and deduplicate."""
    active_sources = _resolve_sources(sources)
    overfetch = limit * 2

    warnings: list[ProviderWarning] = []
    result_lists: list[list[AcademicPaper]] = []
    sources_used: list[str] = []

    async def _search_s2() -> None:
        try:
            results = await search_semanticscholar(
                query,
                limit=overfetch,
                year_from=year_from,
                year_to=year_to,
                fields_of_study=fields_of_study,
                venue=venue,
                open_access_only=open_access_only,
            )
            if results:
                result_lists.append(results)
                sources_used.append("semanticscholar")
            else:
                msg = "Semantic Scholar returned empty (may be rate limited or no matches)"
                logger.info(msg)
                warnings.append(ProviderWarning(provider="semanticscholar", error=msg, error_type="empty_results"))
        except Exception as e:
            logger.warning(f"Semantic Scholar search failed: {e}")
            warnings.append(
                ProviderWarning(
                    provider="semanticscholar",
                    error=str(e)[:200],
                    error_type=type(e).__name__,
                )
            )

    async def _search_arxiv_fn() -> None:
        try:
            results = await search_arxiv(
                query,
                limit=overfetch,
                year_from=year_from,
                year_to=year_to,
                fields_of_study=fields_of_study,
            )
            if results:
                result_lists.append(results)
                sources_used.append("arxiv")
            else:
                msg = "ArXiv returned empty (no matches)"
                logger.info(msg)
                warnings.append(ProviderWarning(provider="arxiv", error=msg, error_type="empty_results"))
        except Exception as e:
            logger.warning(f"ArXiv search failed: {e}")
            warnings.append(
                ProviderWarning(
                    provider="arxiv",
                    error=str(e)[:200],
                    error_type=type(e).__name__,
                )
            )

    async def _search_openalex_fn() -> None:
        try:
            results = await search_openalex(
                query,
                limit=overfetch,
                year_from=year_from,
                year_to=year_to,
                fields_of_study=fields_of_study,
                open_access_only=open_access_only,
            )
            if results:
                result_lists.append(results)
                sources_used.append("openalex")
            else:
                msg = "OpenAlex returned empty (no matches)"
                logger.info(msg)
                warnings.append(ProviderWarning(provider="openalex", error=msg, error_type="empty_results"))
        except Exception as e:
            logger.warning(f"OpenAlex search failed: {e}")
            warnings.append(
                ProviderWarning(
                    provider="openalex",
                    error=str(e)[:200],
                    error_type=type(e).__name__,
                )
            )

    async def _search_crossref_fn() -> None:
        try:
            results = await search_crossref(
                query,
                limit=overfetch,
                year_from=year_from,
                year_to=year_to,
            )
            if results:
                result_lists.append(results)
                sources_used.append("crossref")
            else:
                msg = "CrossRef returned empty (no matches)"
                logger.info(msg)
                warnings.append(ProviderWarning(provider="crossref", error=msg, error_type="empty_results"))
        except Exception as e:
            logger.warning(f"CrossRef search failed: {e}")
            warnings.append(
                ProviderWarning(
                    provider="crossref",
                    error=str(e)[:200],
                    error_type=type(e).__name__,
                )
            )

    async def _search_pubmed_fn() -> None:
        try:
            results = await search_pubmed(
                query,
                limit=overfetch,
                year_from=year_from,
                year_to=year_to,
            )
            if results:
                result_lists.append(results)
                sources_used.append("pubmed")
            else:
                msg = "PubMed returned empty (no matches)"
                logger.info(msg)
                warnings.append(ProviderWarning(provider="pubmed", error=msg, error_type="empty_results"))
        except Exception as e:
            logger.warning(f"PubMed search failed: {e}")
            warnings.append(
                ProviderWarning(
                    provider="pubmed",
                    error=str(e)[:200],
                    error_type=type(e).__name__,
                )
            )

    async def _search_core_fn() -> None:
        try:
            results = await search_core(
                query,
                limit=overfetch,
                year_from=year_from,
                year_to=year_to,
                open_access_only=open_access_only,
            )
            if results:
                result_lists.append(results)
                sources_used.append("core")
            else:
                msg = "CORE returned empty (no API key or no matches)"
                logger.info(msg)
                warnings.append(ProviderWarning(provider="core", error=msg, error_type="empty_results"))
        except Exception as e:
            logger.warning(f"CORE search failed: {e}")
            warnings.append(
                ProviderWarning(
                    provider="core",
                    error=str(e)[:200],
                    error_type=type(e).__name__,
                )
            )

    # Build task list based on resolved sources
    tasks = []
    if "semanticscholar" in active_sources:
        tasks.append(_search_s2())
    if "arxiv" in active_sources:
        tasks.append(_search_arxiv_fn())
    if "openalex" in active_sources:
        tasks.append(_search_openalex_fn())
    if "crossref" in active_sources:
        tasks.append(_search_crossref_fn())
    if "pubmed" in active_sources:
        tasks.append(_search_pubmed_fn())
    if "core" in active_sources:
        tasks.append(_search_core_fn())

    await asyncio.gather(*tasks)

    if not result_lists:
        return AcademicSearchResponse(
            query=query,
            results=[],
            total_results=0,
            sources_used=[],
            warnings=warnings or None,
        )

    merged = _merge_papers(result_lists)
    merged = _sort_papers(merged, sort)
    final = merged[:limit]

    emit_observability_event(
        logger,
        "academic_search.response",
        query=query,
        sources_used=sources_used,
        merged_count=len(merged),
        final_count=len(final),
        warnings_count=len(warnings),
    )

    return AcademicSearchResponse(
        query=query,
        results=final,
        total_results=len(final),
        sources_used=sources_used,
        warnings=warnings or None,
    )