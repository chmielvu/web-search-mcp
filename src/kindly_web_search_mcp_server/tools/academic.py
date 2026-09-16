from __future__ import annotations

import hashlib
import json
import logging
from typing import Annotated, Any, Literal

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from ..analytics.producers import emit_tool_observability_event
from ..cache import get_query_cache, provider_cache_key
from ..errors import raise_tool_error
from ..models import AcademicSearchResponse, fetch_next
from ..utils.text_clean import clean_query as normalize_query
from ._helpers import _academic_search_flight, _record_tool_failure, _record_tool_success

LOGGER = logging.getLogger(__name__)


def _with_next_hints[T: dict](response: T) -> T:
    next_hints = fetch_next(
        [
            u
            for p in response.get("results", [])
            if isinstance(p, dict)
            for u in [p.get("url") or p.get("pdf_url")]
            if isinstance(u, str) and u.strip()
        ],
        why="Read the paper page or PDF for full text.",
        confidence="medium",
    )
    if next_hints:
        response["next"] = next_hints
    return response


async def academic_search(
    query: Annotated[
        str,
        Field(
            description="Academic search query. Use technical terminology for best results. Required unless cited_by_paper_id, references_paper_id, or author_id is provided."
        ),
    ] = "",
    limit: Annotated[
        int, Field(ge=1, le=20, description="Maximum papers to return (1-20, default 5).")
    ] = 5,
    sources: Annotated[
        list[str] | None,
        Field(
            description="Restrict providers, e.g. ['arxiv','pubmed']; default: arxiv + semanticscholar + researchgate."
        ),
    ] = None,
    source_type: Annotated[
        Literal["general", "polish", "archive"] | None,
        Field(
            description="Source group: 'general' (international indexes, default), 'polish' (RAD-on, Biblioteka Nauki, PBN), or 'archive' (Polona, dLibra, RDS, Europeana)."
        ),
    ] = None,
    year_from: Annotated[
        int | None, Field(description="Filter papers published in or after this year.")
    ] = None,
    year_to: Annotated[
        int | None, Field(description="Filter papers published in or before this year.")
    ] = None,
    fields_of_study: Annotated[
        list[str] | None,
        Field(
            description="Filter by academic discipline (e.g. ['Computer Science', 'Biology']); uses Semantic Scholar field taxonomy."
        ),
    ] = None,
    venue: Annotated[
        str | None,
        Field(description="Filter by publication venue name (e.g. 'Nature', 'ICML')."),
    ] = None,
    open_access_only: Annotated[
        bool,
        Field(description="Only return papers with freely available full text."),
    ] = False,
    sort: Annotated[
        Literal["relevance", "citations", "date"],
        Field(description="Result ordering: 'relevance' (default), 'citations', or 'date'."),
    ] = "relevance",
    cited_by_paper_id: Annotated[
        str | None,
        Field(
            description="Return papers citing this work (OpenAlex/S2 ID, DOI, or arXiv ID); routes to citation-graph providers."
        ),
    ] = None,
    references_paper_id: Annotated[
        str | None,
        Field(description="Return this work's bibliography (OpenAlex/S2)."),
    ] = None,
    author_id: Annotated[
        str | None,
        Field(description="Restrict to an author (OpenAlex ID/ORCID or S2 author ID)."),
    ] = None,
    ctx: Context = CurrentContext(),
) -> AcademicSearchResponse:
    """Search scholarly sources with cross-source deduplication: peer-reviewed
    papers, citations, and academic literature.

    WHEN TO USE:
    - Research questions requiring peer-reviewed papers or scientific citations.
    - When standard web search returns non-scholarly blog posts or commercial
      content.
    - Citation-graph traversal: who cites a paper, a paper's references, an
      author's works.

    WHEN NOT TO USE:
    - General web content (use web_search).
    - Reading full paper text of a known URL (use fetch on pdf_url).

    RETURNS:
    - results[]: papers with title, authors, abstract, year, venue, citations,
      url, pdf_url, source, is_open_access, and highlights[].
    - total_results and sources_used.
    - next: suggested fetch calls to read open-access full text.

    CHAINING: call fetch on pdf_url or fulltext_url of an open-access paper
    to read its full text.

    Args:
        query: Academic search query. Use technical terminology for best
            results. Required unless cited_by_paper_id, references_paper_id,
            or author_id is provided.
        limit: Maximum papers to return (1-20, default 5).
        sources: Restrict providers, e.g. ['arxiv','pubmed']. Default:
            arxiv + semanticscholar + researchgate.
        source_type: Source group to query — "general" (default; international
            scholarly indexes), "polish" (Polish scholarly sources: RAD-on,
            Biblioteka Nauki, PBN), or "archive" (historical/digital archives:
            Polona, dLibra libraries, RDS Dataverse, Europeana).
        year_from: Filter papers published in or after this year.
        year_to: Filter papers published in or before this year.
        fields_of_study: Filter by academic discipline (e.g., ["Computer
            Science", "Biology"]). Uses Semantic Scholar field taxonomy.
        venue: Filter by publication venue name (e.g., "Nature", "ICML").
        open_access_only: Only return papers with freely available full text.
        sort: Result ordering — "relevance" (default), "citations", or "date".
        cited_by_paper_id: Return papers citing this work (OpenAlex/S2 ID,
            DOI, or arXiv ID). Routes to citation-graph providers.
        references_paper_id: Return this work's bibliography (OpenAlex/S2).
        author_id: Restrict to an author (OpenAlex ID/ORCID or S2 author ID).
            When any citation-graph/author filter is set, only providers that
            support it are queried unless `sources` overrides them.
    """
    if not query.strip() and not (cited_by_paper_id or references_paper_id or author_id):
        _record_tool_failure("academic_search")
        raise_tool_error(
            ValueError(
                "query is required unless cited_by_paper_id, references_paper_id, "
                "or author_id is provided."
            ),
            provider="academic_search",
        )

    await ctx.report_progress(progress=5, total=100, message="Checking cache...")
    await ctx.info(f"Academic search: {query[:80]}...")

    normalized_query = normalize_query(query)
    sources_key = provider_cache_key(sources)

    emit_tool_observability_event(
        LOGGER,
        "academic_search",
        "request",
        query=query,
        normalized_query=normalized_query,
        limit=limit,
        sources=sources,
        source_type=source_type,
        sources_key=sources_key,
        year_from=year_from,
        year_to=year_to,
        fields_of_study=fields_of_study,
        venue=venue,
        open_access_only=open_access_only,
        sort=sort,
    )

    filter_params: dict[str, Any] = {
        "year_from": year_from,
        "year_to": year_to,
        "fields_of_study": sorted(fields_of_study) if fields_of_study else None,
        "venue": venue,
        "open_access_only": open_access_only,
        "sort": sort,
        "source_type": source_type,
        "cited_by": cited_by_paper_id,
        "references": references_paper_id,
        "author": author_id,
    }

    filter_key = json.dumps(filter_params, sort_keys=True, default=str)
    filter_digest = hashlib.sha256(filter_key.encode("utf-8")).hexdigest()[:16]
    cache_providers_key = f"academic:{sources_key}:{filter_digest}"

    try:
        exact_cache = get_query_cache()
        exact_cached = exact_cache.lookup(
            normalized_query=normalized_query,
            num_results=limit,
            rewrite_enabled=True,
            search_mode="academic",
            providers_key=cache_providers_key,
        )
        if exact_cached:
            LOGGER.debug("Exact query cache hit for academic search: %s", query[:100])
            # Copy before mutating so concurrent requests don't corrupt the
            # shared cached object.
            exact_cached = dict(exact_cached)
            exact_cached["query"] = query
            emit_tool_observability_event(
                LOGGER,
                "academic_search",
                "response",
                cache_hit="exact",
                query=query,
                result_count=len(exact_cached.get("results", [])),
                sources_used=exact_cached.get("sources_used", []),
            )
            _record_tool_success(
                "academic_search",
                input_query=query,
                output_result_count=len(exact_cached.get("results", [])),
            )
            return AcademicSearchResponse.model_validate(_with_next_hints(exact_cached))
    except Exception as e:
        LOGGER.warning("Exact query cache lookup failed for academic search: %s", e)

    await ctx.report_progress(progress=20, total=100, message="Searching academic sources...")

    async def _execute_academic_search() -> dict:
        from ..search.academic.academic_search_orchestrator import run_academic_search

        result = await run_academic_search(
            query,
            limit=limit,
            sources=sources,
            source_type=source_type,
            year_from=year_from,
            year_to=year_to,
            fields_of_study=fields_of_study,
            venue=venue,
            open_access_only=open_access_only,
            sort=sort,
            cited_by_paper_id=cited_by_paper_id,
            references_paper_id=references_paper_id,
            author_id=author_id,
        )
        response = result.model_dump(exclude_none=True)

        try:
            exact_cache = get_query_cache()
            exact_cache.store(
                normalized_query=normalized_query,
                num_results=limit,
                rewrite_enabled=True,
                response=response,
                search_mode="academic",
                providers_key=cache_providers_key,
            )
            LOGGER.debug("Stored exact query cache for academic search: %s", query[:100])
        except Exception as e:
            LOGGER.warning("Exact query cache write failed for academic search: %s", e)

        return response

    try:
        flight_key = _academic_search_flight.make_key(
            normalized_query, limit, sources_key, filter_digest
        )
        response = await _academic_search_flight.do(
            flight_key,
            _execute_academic_search,
            timeout_seconds=60.0,
            initiator_timeout_seconds=60.0,
        )

        _record_tool_success(
            "academic_search",
            input_query=query,
            output_result_count=len(response.get("results", [])),
        )
        emit_tool_observability_event(
            LOGGER,
            "academic_search",
            "response",
            cache_hit="miss",
            query=query,
            result_count=len(response.get("results", [])),
            sources_used=response.get("sources_used", []),
            source_types_used=response.get("source_types_used", []),
        )
        return AcademicSearchResponse.model_validate(_with_next_hints(response))
    except Exception as e:
        LOGGER.warning("Academic search failed: %s", e)
        _record_tool_failure("academic_search")
        emit_tool_observability_event(
            LOGGER,
            "academic_search",
            "error",
            level=30,
            query=query,
            error=str(e)[:200],
        )

        raise_tool_error(e, provider="academic_search")
