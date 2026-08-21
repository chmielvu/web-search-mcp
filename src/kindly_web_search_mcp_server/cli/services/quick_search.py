from __future__ import annotations

from typing import Any

from ...quick_web_search import _quick_web_search_impl


async def fetch_quick_web_search_payload(
    search_queries: list[str],
    objective: str,
    *,
    max_results: int | None = None,
    max_chars_total: int | None = None,
    max_chars_per_result: int | None = None,
    client_model: str | None = None,
    session_id: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    after_date: str | None = None,
    location: str | None = None,
    max_age_seconds: int | None = None,
    timeout_seconds: float | None = None,
    disable_cache_fallback: bool | None = None,
) -> dict[str, Any]:
    response = await _quick_web_search_impl(
        search_queries,
        objective=objective,
        max_results=max_results,
        max_chars_total=max_chars_total,
        max_chars_per_result=max_chars_per_result,
        client_model=client_model,
        session_id=session_id,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        after_date=after_date,
        location=location,
        max_age_seconds=max_age_seconds,
        timeout_seconds=timeout_seconds,
        disable_cache_fallback=disable_cache_fallback,
    )
    return response.model_dump(exclude_none=True)
