from __future__ import annotations

import uuid
from typing import Any

from ...search.contracts import WebSearchRequest
from ...search.diagnostics import build_diagnostics
from ...search.options import build_search_options
from ...search.service import execute_web_search
from ...utils.http_client import get_http_client


async def fetch_web_search_payload(
    query: str,
    *,
    num_results: int,
    rewrite: bool,
    research_goal: str,
    result_offset: int = 0,
    site_filters: list[str] | None = None,
    domain_filters: list[str] | None = None,
    domain_boost: list[str] | None = None,
    domain_block: list[str] | None = None,
    diagnostics: bool = False,
    **_obsolete_options: object,
) -> dict[str, Any]:
    search_options = build_search_options(
        result_offset=result_offset,
        site_filters=[*(site_filters or []), *(domain_filters or [])],
    )
    request = WebSearchRequest(
        query=query,
        research_goal=research_goal,
        num_results=num_results,
        rewrite=rewrite,
        options=search_options,
    )
    run_key = str(uuid.uuid4())
    response, run = await execute_web_search(
        request,
        http_client=await get_http_client(),
        run_key=run_key,
        tool_call_id=run_key,
        return_diagnostics=True,
    )
    payload = response.model_dump(exclude_none=True)
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
