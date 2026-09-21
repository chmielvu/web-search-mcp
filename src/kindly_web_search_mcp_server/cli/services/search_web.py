from __future__ import annotations

import uuid
from typing import Any, cast

from ...search.contracts import SearchRun, WebSearchRequest
from ...search.diagnostics import build_diagnostics
from ...search.options import SearchOptions
from ...search.service import execute_web_search
from ...search.types import SearchRunResult
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
    cursor: str | None = None,
    run_key: str | None = None,
) -> dict[str, Any]:
    if cursor and str(cursor).strip():
        if not (run_key and str(run_key).strip()):
            raise ValueError("run_key is required together with cursor.")
        from ...utils.public_output import page_overflow_cursor

        public = page_overflow_cursor(str(run_key).strip(), str(cursor).strip())
        dumped = public.model_dump(exclude_none=True)
        return dumped

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
    search_options = SearchOptions(
        temporal=temporal_window if not temporal_window.is_empty else None,
        language=(locale_spec.language if locale_spec else None),
        region=(locale_spec.region if locale_spec else None),
    ).validate()
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
    _response, run = cast(tuple[SearchRunResult, SearchRun], search_result)
    from ...utils.public_output import to_public_web_search_from_run

    payload = to_public_web_search_from_run(run).model_dump(exclude_none=True)
    payload["run_key"] = run_key

    if diagnostics:
        diag = build_diagnostics(run, run.diagnostics.total_latency_ms or 0.0)
        payload["_diagnostics"] = (
            diag.model_dump(mode="json") if hasattr(diag, "model_dump") else {}
        )
    return payload
