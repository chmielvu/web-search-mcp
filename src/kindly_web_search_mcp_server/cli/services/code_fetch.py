from __future__ import annotations

from typing import Any

from ...tools.code_search.exploration import code_fetch


async def fetch_code_fetch_payload(
    repository: str,
    *,
    query: str | None = None,
    path: str | None = None,
    symbol: str | None = None,
    regexp: bool = False,
    max_matches: int = 25,
    context_lines: int = 3,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    response = await code_fetch(
        repository=repository,
        query=query,
        path=path,
        symbol=symbol,
        regexp=regexp,
        max_matches=max_matches,
        context_lines=context_lines,
        start_line=start_line,
        end_line=end_line,
        ctx=None,
    )
    return response.model_dump(exclude_none=True)
