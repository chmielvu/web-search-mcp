from __future__ import annotations

import uuid
from typing import Any

from ...search.contracts import WebSearchRequest
from ...search.diagnostics import build_diagnostics
from ...search.options import build_search_options
from ...search.service import execute_web_search
from ...utils.http_client import get_http_client


async def fetch_web_search_payload(
    query: str | list[str],
    *,
    rewrite: bool,
    research_goal: str,
    site_filters: list[str] | None = None,
    domain_filters: list[str] | None = None,
    domain_boost: list[str] | None = None,
    domain_block: list[str] | None = None,
    reranking_instructions: str | None = None,
    diagnostics: bool = False,
    **_obsolete_options: object,
) -> dict[str, Any]:
    if isinstance(query, list):
        cleaned_queries = tuple(q.strip() for q in query if q and q.strip())[:4]
        if not cleaned_queries:
            raise ValueError("query must contain at least one non-blank string.")
        primary_query = cleaned_queries[0]
        seed_queries = cleaned_queries
    elif isinstance(query, str) and query.strip():
        primary_query = query.strip()
        seed_queries = (primary_query,)
    else:
        raise ValueError("query must be non-blank.")

    search_options = build_search_options(
        site_filters=[*(site_filters or []), *(domain_filters or [])],
    )
    request = WebSearchRequest(
        query=primary_query,
        queries=seed_queries,
        research_goal=research_goal,
        rewrite=rewrite,
        options=search_options,
        reranking_instructions=reranking_instructions,
    )
    run_key = str(uuid.uuid4())
    response, run = await execute_web_search(
        request,
        http_client=await get_http_client(),
        run_key=run_key,
        tool_call_id=run_key,
        return_diagnostics=True,
        schedule_judges=False,
    )  # type: ignore[assignment,misc]
    payload = response.model_dump(exclude_none=True)
    payload["run_key"] = run_key
    if domain_boost or domain_block:
        from ...tools._helpers import _apply_domain_filters

        payload["results"] = _apply_domain_filters(
            payload.get("results", []), domain_boost, domain_block
        )
    if diagnostics:
        diag = build_diagnostics(run, run.diagnostics.total_latency_ms or 0.0)
        payload["_diagnostics"] = (
            diag.model_dump(mode="json") if hasattr(diag, "model_dump") else {}
        )
    return payload
