from __future__ import annotations

import uuid
from typing import Any, cast

from ...models import WebSearchResponse
from ...search.contracts import SearchRun, WebSearchRequest
from ...search.diagnostics import build_diagnostics
from ...search.options import build_search_options
from ...search.service import execute_web_search
from ...utils.http_client import get_http_client


async def fetch_web_search_payload(
    query: str | list[str],
    *,
    rewrite: bool,
    research_goal: str,
    domain_boost: list[str] | None = None,
    reranking_instructions: str | None = None,
    date_range: str | None = None,
    after_date: str | None = None,
    before_date: str | None = None,
    language: str | None = None,
    region: str | None = None,
    gl: str | None = None,
    include_undated: bool | None = None,
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

    from ...search.filters import FilterValidationError, normalize_locale, resolve_window

    if after_date and date_range:
        date_range = None
    try:
        temporal_window = resolve_window(
            date_range=date_range,
            after_date=after_date,
            before_date=before_date,
        )
        locale_spec = normalize_locale(language=language, region=region, gl=gl)
    except FilterValidationError as exc:
        raise ValueError(str(exc)) from exc

    search_options = build_search_options(
        locale_spec=locale_spec if (locale_spec.language or locale_spec.region) else None,
        temporal_window=temporal_window if not temporal_window.is_empty else None,
    )
    request = WebSearchRequest(
        query=primary_query,
        queries=seed_queries,
        research_goal=research_goal,
        rewrite=rewrite,
        options=search_options,
        reranking_instructions=reranking_instructions,
        include_undated=include_undated,
        domain_boost=tuple(domain_boost or []),
    )
    run_key = str(uuid.uuid4())
    search_result = await execute_web_search(
        request,
        http_client=await get_http_client(),
        run_key=run_key,
        tool_call_id=run_key,
        return_diagnostics=True,
        schedule_judges=False,
    )
    response, run = cast(tuple[WebSearchResponse, SearchRun], search_result)
    payload = response.model_dump(exclude_none=True)
    payload["run_key"] = run_key

    if diagnostics:
        diag = build_diagnostics(run, run.diagnostics.total_latency_ms or 0.0)
        payload["_diagnostics"] = (
            diag.model_dump(mode="json") if hasattr(diag, "model_dump") else {}
        )
    return payload
