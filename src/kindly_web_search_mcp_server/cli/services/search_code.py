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
    huggingface_type: str = "both",
    huggingface_sort_by: str = "similarity",
    huggingface_hybrid: bool = False,
    huggingface_min_likes: int = 0,
    huggingface_min_downloads: int = 0,
    huggingface_task: str | None = None,
    huggingface_license: str | None = None,
    huggingface_language: str | None = None,
    huggingface_modified_after: str | None = None,
    huggingface_min_param_count: int = 0,
    huggingface_max_param_count: int | None = None,
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
        huggingface_type=huggingface_type,
        huggingface_sort_by=huggingface_sort_by,
        huggingface_hybrid=huggingface_hybrid,
        huggingface_min_likes=huggingface_min_likes,
        huggingface_min_downloads=huggingface_min_downloads,
        huggingface_task=huggingface_task,
        huggingface_license=huggingface_license,
        huggingface_language=huggingface_language,
        huggingface_modified_after=huggingface_modified_after,
        huggingface_min_param_count=huggingface_min_param_count,
        huggingface_max_param_count=huggingface_max_param_count,
        ctx=cast(Context, None),
    )
    return response.model_dump(exclude_none=True)
