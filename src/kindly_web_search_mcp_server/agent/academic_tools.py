from __future__ import annotations

from typing import Any

from langchain.tools import tool

from kindly_web_search_mcp_server.search.academic_search_orchestrator import (
    run_academic_search,
)

from .models import AcademicSearchInput


async def _academic_search(
    query: str,
    limit: int,
    sources: list[str] | None,
    year_from: int | None,
    year_to: int | None,
    fields_of_study: list[str] | None,
    venue: str | None,
    open_access_only: bool,
    sort: str,
) -> dict[str, Any]:
    response = await run_academic_search(
        query,
        limit=limit,
        sources=sources,
        year_from=year_from,
        year_to=year_to,
        fields_of_study=fields_of_study,
        venue=venue,
        open_access_only=open_access_only,
        sort=sort,
    )
    return response.model_dump(exclude_none=True)


academic_search = tool(
    "academic_search",
    args_schema=AcademicSearchInput,
    description=(
        "Search scholarly sources. Use for papers, citations, and academic evidence "
        "instead of general web search."
    ),
)(_academic_search)


def get_academic_tools() -> list[Any]:
    return [academic_search]
