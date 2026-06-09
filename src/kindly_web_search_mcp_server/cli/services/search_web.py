from __future__ import annotations

from typing import Any

from ...search.options import build_search_options
from ...search.pipeline import run_search_pipeline


async def fetch_web_search_payload(
    query: str,
    *,
    num_results: int,
    rewrite: bool,
    providers: list[str] | None,
    research_goal: str | None,
    result_offset: int = 0,
    searxng_categories: list[str] | None = None,
    searxng_engines: list[str] | None = None,
    searxng_language: str | None = None,
    searxng_pageno: int = 1,
    searxng_time_range: str | None = None,
    searxng_safesearch: int | None = None,
    site_filters: list[str] | None = None,
    domain_filters: list[str] | None = None,
) -> dict[str, Any]:
    search_options = build_search_options(
        result_offset=result_offset,
        searxng_categories=searxng_categories,
        searxng_engines=searxng_engines,
        searxng_language=searxng_language,
        searxng_pageno=searxng_pageno,
        searxng_time_range=searxng_time_range,
        searxng_safesearch=searxng_safesearch,
        site_filters=site_filters,
        domain_filters=domain_filters,
    )
    response = await run_search_pipeline(
        query,
        num_results=num_results,
        rewrite=rewrite,
        providers=providers,
        research_goal=research_goal,
        search_options=search_options,
    )
    return response.model_dump(exclude_none=True)
