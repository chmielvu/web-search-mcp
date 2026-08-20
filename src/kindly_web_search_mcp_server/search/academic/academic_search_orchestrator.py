"""Academic search orchestrator: parallel provider search → dedup → merge → response.

Follows the same pattern as search/orchestrator.py (web search):
1. Resolve which providers fire (default: arxiv + semanticscholar; source_type groups)
2. Fire providers in parallel via asyncio.gather (with per-provider throttling)
3. Normalize results to AcademicPaper
4. Deduplicate by DOI > ArXiv ID > PubMed ID > CORE ID > title
5. Sort (relevance default, or citation count / date)
6. Return AcademicSearchResponse with per-provider warnings

Providers:
- general: arxiv, semanticscholar, openalex, crossref, pubmed, core
- polish:   radon, bn (Biblioteka Nauki), pbn
- archive:  polona, dlibra, rds, europeana

Resilience: ProviderResilience tracks per-provider throttling, consecutive 429s,
failures, and zero-result runs; providers are disabled for the run after 3
consecutive 429s/failures and skipped after 3 consecutive zero-result queries.
"""

from __future__ import annotations

import asyncio
import logging

from ...models import AcademicPaper, AcademicSearchResponse, ProviderWarning
from ...utils.observability import emit_observability_event
from .academic_arxiv import search_arxiv
from .academic_core import search_core
from .academic_crossref import search_crossref
from .academic_openalex import search_openalex
from .academic_pubmed import search_pubmed
from .academic_s2 import search_semanticscholar
from .provider_resilience import ProviderResilience

logger = logging.getLogger(__name__)

# Provider name → source_type. New providers must be registered here.
PROVIDER_SOURCE_TYPES: dict[str, str] = {
    "arxiv": "general",
    "semanticscholar": "general",
    "openalex": "general",
    "crossref": "general",
    "pubmed": "general",
    "core": "general",
    "researchgate": "general",
    "radon": "polish",
    "bn": "polish",
    "pbn": "polish",
    "polona": "archive",
    "dlibra": "archive",
    "rds": "archive",
    "europeana": "archive",
}

# Canonical provider name → accepted aliases (lowercased, stripped of -_/space).
PROVIDER_ALIASES: dict[str, set[str]] = {
    "semanticscholar": {"semanticscholar", "s2", "semantic"},
    "arxiv": {"arxiv"},
    "openalex": {"openalex", "alex", "oa"},
    "crossref": {"crossref", "cr", "doi"},
    "pubmed": {"pubmed", "pm", "medline"},
    "core": {"core"},
    "researchgate": {"researchgate", "rg"},
    "radon": {"radon", "rad-on", "polon"},
    "bn": {"bn", "bibliotekanauki", "biblioteka-nauki", "biblioteka_nauki"},
    "pbn": {"pbn"},
    "polona": {"polona"},
    "dlibra": {"dlibra", "wbc", "jbc", "fbc"},
    "rds": {"rds", "dataverse"},
    "europeana": {"europeana"},
}

# source_type → default providers when the caller asks for a group.
SOURCE_TYPE_DEFAULTS: dict[str, list[str]] = {
    # general: cheap, always-on, broad-coverage indexes (S2 rate-limits fast;
    # ResearchGate is OpenAlex-aliased and benefits from S2_API_KEY being unset
    # to fall through to arxiv-only when rate-limited).
    "general": ["arxiv", "semanticscholar", "researchgate"],
    # polish: nationwide registry (RAD-on) + BN (no key); PBN needs PBN_APP_*.
    "polish": ["radon", "bn", "pbn"],
    # archive: Polona (fulltext) + RDS Dataverse (datasets) + Europeana (key).
    "archive": ["polona", "rds", "europeana"],
}

# Per-run resilience state (single event loop; module-level is fine for the
# server process, matching the existing singleflight/cache patterns).
_resilience = ProviderResilience()


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


def _merge_two(existing: AcademicPaper, incoming: AcademicPaper) -> AcademicPaper:
    """Merge two papers that refer to the same work, keeping the best fields."""
    return AcademicPaper(
        title=existing.title if len(existing.title) >= len(incoming.title) else incoming.title,
        authors=existing.authors
        if len(existing.authors) >= len(incoming.authors)
        else incoming.authors,
        abstract=existing.abstract or incoming.abstract,
        year=existing.year if existing.year is not None else incoming.year,
        venue=existing.venue or incoming.venue,
        citations=existing.citations if existing.citations is not None else incoming.citations,
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
        source_type=existing.source_type,
        date_descriptive=existing.date_descriptive or incoming.date_descriptive,
        highlights=existing.highlights or incoming.highlights,
        fulltext_url=existing.fulltext_url or incoming.fulltext_url,
    )


def _merge_dicts(a: dict[str, str] | None, b: dict[str, str] | None) -> dict[str, str] | None:
    if not a and not b:
        return None
    merged = {}
    if a:
        merged.update(a)
    if b:
        merged.update(b)
    return merged or None


def _sort_papers(papers: list[AcademicPaper], sort: str) -> list[AcademicPaper]:
    """Sort papers by relevance (default), citation count, or date."""
    if sort == "citations":
        return sorted(papers, key=lambda p: p.citations or 0, reverse=True)
    if sort == "date":
        return sorted(papers, key=lambda p: p.year or 0, reverse=True)
    return papers


def _resolve_sources(
    sources: list[str] | None,
    source_type: str | None = None,
) -> list[str]:
    """Resolve which providers to query.

    - source_type given: default providers for that group (sources may add more).
    - sources given: explicit providers (aliases resolved; unknown → ValueError).
    - neither: default arxiv + semanticscholar.
    """
    if source_type is not None and source_type not in SOURCE_TYPE_DEFAULTS:
        raise ValueError(
            f"Unknown source_type {source_type!r}; expected one of "
            f"{sorted(SOURCE_TYPE_DEFAULTS)}"
        )

    if sources:
        requested = {s.lower().replace("-", "").replace("_", "").replace(" ", "") for s in sources}
        normalized: list[str] = []
        unknown: list[str] = []
        for r in requested:
            matched = None
            for canonical, aliases in PROVIDER_ALIASES.items():
                if r in aliases:
                    matched = canonical
                    break
            if matched is not None:
                if matched not in normalized:
                    normalized.append(matched)
            else:
                unknown.append(r)
        if unknown:
            raise ValueError(
                f"Unknown academic source(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(sorted(PROVIDER_ALIASES))}"
            )
        if source_type is not None:
            # Explicit sources win; keep only those matching the requested type.
            normalized = [s for s in normalized if PROVIDER_SOURCE_TYPES[s] == source_type]
        return normalized or SOURCE_TYPE_DEFAULTS.get(source_type or "general", ["arxiv", "semanticscholar"])

    if source_type is not None:
        return list(SOURCE_TYPE_DEFAULTS[source_type])
    return ["arxiv", "semanticscholar"]


async def run_academic_search(
    query: str,
    *,
    limit: int = 5,
    sources: list[str] | None = None,
    source_type: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    fields_of_study: list[str] | None = None,
    venue: str | None = None,
    open_access_only: bool = False,
    sort: str = "relevance",
) -> AcademicSearchResponse:
    """Execute academic search across all providers in parallel, merge, and deduplicate."""
    active_sources = _resolve_sources(sources, source_type)
    overfetch = limit * 2

    warnings: list[ProviderWarning] = []
    result_lists: list[list[AcademicPaper]] = []
    sources_used: list[str] = []

    async def _run_provider(name: str, fn) -> None:
        """Throttle, run one provider, record resilience state, collect results."""
        if _resilience.is_disabled(name):
            warnings.append(
                ProviderWarning(
                    provider=name,
                    error="Provider disabled for this run (consecutive 429s/failures)",
                    error_type="disabled",
                )
            )
            return
        await _resilience.throttle(name)
        try:
            results = await fn()
        except Exception as e:
            logger.warning("%s search failed: %s", name, e)
            if _resilience.record_failure(name):
                warnings.append(
                    ProviderWarning(
                        provider=name,
                        error=f"Provider disabled after repeated failures: {e}",
                        error_type="disabled",
                    )
                )
            else:
                warnings.append(
                    ProviderWarning(
                        provider=name,
                        error=str(e)[:200],
                        error_type=type(e).__name__,
                    )
                )
            return

        if results:
            _resilience.record_success(name, len(results))
            result_lists.append(results)
            sources_used.append(name)
        else:
            _resilience.record_success(name, 0)
            if _resilience.record_zero_results(name):
                msg = f"{name} returned empty (skipping for remaining queries this run)"
            else:
                msg = f"{name} returned empty (no matches or rate limited)"
            logger.info(msg)
            warnings.append(
                ProviderWarning(provider=name, error=msg, error_type="empty_results")
            )

    async def _search_s2() -> list[AcademicPaper]:
        return await search_semanticscholar(
            query,
            limit=overfetch,
            year_from=year_from,
            year_to=year_to,
            fields_of_study=fields_of_study,
            venue=venue,
            open_access_only=open_access_only,
        )

    async def _search_arxiv_fn() -> list[AcademicPaper]:
        return await search_arxiv(
            query,
            limit=overfetch,
            year_from=year_from,
            year_to=year_to,
            fields_of_study=fields_of_study,
        )

    async def _search_openalex_fn() -> list[AcademicPaper]:
        return await search_openalex(
            query,
            limit=overfetch,
            year_from=year_from,
            year_to=year_to,
            fields_of_study=fields_of_study,
            open_access_only=open_access_only,
        )

    async def _search_crossref_fn() -> list[AcademicPaper]:
        return await search_crossref(
            query,
            limit=overfetch,
            year_from=year_from,
            year_to=year_to,
        )

    async def _search_pubmed_fn() -> list[AcademicPaper]:
        return await search_pubmed(
            query,
            limit=overfetch,
            year_from=year_from,
            year_to=year_to,
        )

    async def _search_core_fn() -> list[AcademicPaper]:
        return await search_core(
            query,
            limit=overfetch,
            year_from=year_from,
            year_to=year_to,
            open_access_only=open_access_only,
        )

    async def _search_radon_fn() -> list[AcademicPaper]:
        from .academic_radon import search_radon

        return await search_radon(query, limit=overfetch)

    async def _search_bn_fn() -> list[AcademicPaper]:
        from .academic_bn import search_bn

        return await search_bn(query, limit=overfetch)

    async def _search_pbn_fn() -> list[AcademicPaper]:
        from .academic_pbn import search_pbn

        return await search_pbn(query, limit=overfetch)

    async def _search_polona_fn() -> list[AcademicPaper]:
        from .academic_polona import search_polona

        return await search_polona(query, limit=overfetch)

    async def _search_dlibra_fn() -> list[AcademicPaper]:
        from .academic_dlibra import search_dlibra

        return await search_dlibra(query, limit=overfetch)

    async def _search_rds_fn() -> list[AcademicPaper]:
        from .academic_rds import search_rds

        return await search_rds(query, limit=overfetch)

    async def _search_europeana_fn() -> list[AcademicPaper]:
        from .academic_europeana import search_europeana

        return await search_europeana(query, limit=overfetch)

    async def _search_researchgate_fn() -> list[AcademicPaper]:
        from .academic_researchgate import search_researchgate

        return await search_researchgate(
            query,
            limit=overfetch,
            year_from=year_from,
            year_to=year_to,
        )

    _PROVIDER_FNS = {
        "semanticscholar": _search_s2,
        "arxiv": _search_arxiv_fn,
        "openalex": _search_openalex_fn,
        "crossref": _search_crossref_fn,
        "pubmed": _search_pubmed_fn,
        "core": _search_core_fn,
        "radon": _search_radon_fn,
        "bn": _search_bn_fn,
        "pbn": _search_pbn_fn,
        "polona": _search_polona_fn,
        "dlibra": _search_dlibra_fn,
        "rds": _search_rds_fn,
        "europeana": _search_europeana_fn,
        "researchgate": _search_researchgate_fn,
    }

    tasks = [_run_provider(name, _PROVIDER_FNS[name]) for name in active_sources]
    await asyncio.gather(*tasks)

    if not result_lists:
        return AcademicSearchResponse(
            query=query,
            results=[],
            total_results=0,
            sources_used=[],
            source_types_used=[],
            warnings=warnings or None,
        )

    merged = _merge_papers(result_lists)
    merged = _sort_papers(merged, sort)
    final = merged[:limit]

    source_types_used = sorted(
        {PROVIDER_SOURCE_TYPES[s] for s in sources_used if s in PROVIDER_SOURCE_TYPES}
    )

    emit_observability_event(
        logger,
        "academic_search.response",
        query=query,
        sources_used=sources_used,
        source_types_used=source_types_used,
        merged_count=len(merged),
        final_count=len(final),
        warnings_count=len(warnings),
    )

    return AcademicSearchResponse(
        query=query,
        results=final,
        total_results=len(final),
        sources_used=sources_used,
        source_types_used=source_types_used,
        warnings=warnings or None,
    )
