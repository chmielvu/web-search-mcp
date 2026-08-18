from __future__ import annotations

from typing import Any, cast

from fastmcp.server.context import Context

from ...tools.code_search import code_search


async def fetch_code_search_payload(
    query: str,
    *,
    research_goal: str | None,
    repositories: list[str] | None,
    language: str | None,
    path: str | None,
    filename: str | None,
    extension: str | None,
    regexp: bool,
    deep: bool,
    repo_name: str | None,
    library_name: str | None,
    topic: str | None,
    mode: str,
) -> dict[str, Any]:
    response = await code_search(
        query=query,
        research_goal=research_goal,
        repositories=repositories,
        language=language,
        path=path,
        filename=filename,
        extension=extension,
        regexp=regexp,
        deep=deep,
        repo_name=repo_name,
        library_name=library_name,
        topic=topic,
        mode=mode,
        ctx=cast(Context, None),
    )
    return response.model_dump(exclude_none=True)
