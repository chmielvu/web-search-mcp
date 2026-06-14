from __future__ import annotations

import asyncio
from typing import Any

from langchain.tools import tool

from kindly_web_search_mcp_server.content.artifact import ContentArtifact, ContentError
from kindly_web_search_mcp_server.content.batch_orchestrator import (
    BatchParams,
    run_batch_fetch,
)
from kindly_web_search_mcp_server.content.fetch_pipeline import fetch_content_artifact
from kindly_web_search_mcp_server.content.link_discovery import (
    discover_links as discover_page_links,
)
from kindly_web_search_mcp_server.content.options import build_fetch_options
from kindly_web_search_mcp_server.content.summary import (
    create_batch_summaries,
    create_summary,
)
from kindly_web_search_mcp_server.content.windowing import slice_content
from kindly_web_search_mcp_server.utils.observability import emit_observability_event

from .models import (
    BatchGetContentInput,
    DiscoverLinksInput,
    GetContentInput,
)


def _artifact_payload(
    artifact: Any, *, page_content: str, window: dict[str, Any]
) -> dict[str, Any]:
    payload = {
        "input_url": artifact.input_url,
        "normalized_url": artifact.normalized_url,
        "fetched_url": artifact.fetched_url,
        "status": artifact.status,
        "source_type": artifact.source_type,
        "fetch_backend": artifact.fetch_backend,
        "page_content": page_content,
        "window": window,
        "content_type": artifact.content_type,
        "continuation_notice": artifact.continuation_notice,
        "metadata": artifact.metadata,
        "links": artifact.links,
        "diagnostics": artifact.diagnostics,
    }
    if artifact.error is not None:
        payload["error"] = {
            "code": artifact.error.code,
            "message": artifact.error.message,
            "retryable": artifact.error.retryable,
        }
    return payload


async def _get_content(
    url: str,
    char_offset: int,
    char_length: int,
    include_metadata: bool,
    include_links: bool,
    max_links: int,
    strip_selectors: str | None,
    timeout_seconds: float = 120.0,
    summary_mode: str = "none",
    focus_query: str | None = None,
) -> dict[str, Any]:
    options = build_fetch_options(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )
    try:
        artifact = await asyncio.wait_for(
            fetch_content_artifact(url, fetch_options=options),
            timeout=timeout_seconds,
        )
    except (asyncio.TimeoutError, TimeoutError):
        artifact = ContentArtifact(
            input_url=url,
            normalized_url=url,
            fetched_url=None,
            status="error",
            source_type="web",
            fetch_backend="agent_get_content",
            content_type=None,
            markdown="",
            error=ContentError(
                code="timeout",
                message=f"get_content timed out after {timeout_seconds}s",
                retryable=True,
            ),
        )
    try:
        # Light internal event for DuckDB correlation (LC callback usually captures as tool obs too)
        emit_observability_event(
            __import__("logging").getLogger(__name__),
            "agent.content.get_content",
            url=url[:200],
            status=getattr(artifact, "status", None),
            backend=getattr(artifact, "fetch_backend", None),
            content_len=len(getattr(artifact, "markdown", "") or ""),
        )
    except Exception:
        pass
    sliced = slice_content(artifact.markdown, offset=char_offset, length=char_length)
    payload = _artifact_payload(
        artifact,
        page_content=sliced.content,
        window=sliced.window.__dict__,
    )
    if summary_mode in {"brief", "detailed"}:
        payload["summary"] = await create_summary(
            sliced.content,
            mode=summary_mode,
            focus_query=focus_query,
            source_urls=[
                artifact.fetched_url or artifact.normalized_url or artifact.input_url
            ],
        )
    if not include_metadata:
        payload["metadata"] = None
    if not include_links:
        payload["links"] = None
    return payload


async def _batch_get_content(
    urls: list[str] | None,
    cursor: str | None,
    max_concurrency: int,
    per_item_char_length: int,
    total_char_budget: int,
    per_url_timeout_seconds: float,
    include_metadata: bool,
    include_links: bool,
    max_links: int,
    strip_selectors: str | None,
    summary_mode: str = "none",
    focus_query: str | None = None,
) -> dict[str, Any]:
    options = build_fetch_options(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )
    output = await run_batch_fetch(
        urls=urls,
        params=BatchParams(
            max_concurrency=max_concurrency,
            per_item_char_length=per_item_char_length,
            total_char_budget=total_char_budget,
            per_url_timeout_seconds=per_url_timeout_seconds,
        ),
        cursor=cursor,
        fetch_options=options,
    )
    if summary_mode in {"brief", "detailed"}:
        summaries = await create_batch_summaries(
            output["results"],
            mode=summary_mode,
            focus_query=focus_query,
            max_concurrency=max_concurrency,
        )
        return {
            **output,
            "results": [
                {**item, "summary": summaries[idx]}
                for idx, item in enumerate(output["results"])
            ],
        }
    return output


async def _discover_links(
    url: str,
    max_links: int,
    include_external: bool,
    same_domain_only: bool,
    strip_selectors: str | None,
) -> dict[str, Any]:
    return await discover_page_links(
        url,
        max_links=max_links,
        include_external=include_external,
        same_domain_only=same_domain_only,
        strip_selectors=strip_selectors,
    )


get_content = tool(
    "get_content",
    args_schema=GetContentInput,
    description=(
        "Fetch and slice one known URL. Use when a search result already identified a "
        "source and you need bounded page content, metadata, links, or an optional "
        "Gemini summary via summary_mode=brief|detailed."
    ),
)(_get_content)

batch_get_content = tool(
    "batch_get_content",
    args_schema=BatchGetContentInput,
    description=(
        "Fetch multiple URLs with a total character budget. Use for a shortlist of "
        "sources when you need cross-source evidence and, optionally, a Gemini summary "
        "for each returned item via summary_mode=brief|detailed."
    ),
)(_batch_get_content)

discover_links = tool(
    "discover_links",
    args_schema=DiscoverLinksInput,
    description=(
        "Extract outbound links or sitemap links from a known URL. Use for site "
        "exploration and URL expansion."
    ),
)(_discover_links)


def get_content_tools() -> list[Any]:
    return [get_content, batch_get_content, discover_links]
