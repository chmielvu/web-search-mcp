from __future__ import annotations

from typing import Any

from ...search.academic_search_orchestrator import run_academic_search


async def fetch_academic_search_payload(
    query: str,
    *,
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

