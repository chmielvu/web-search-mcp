"""FastMCP facade for the production code-search package."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Annotated

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from opentelemetry import trace
from pydantic import Field

from ...telemetry import SEARCH_QUERY, create_chain_span
from ...utils.observability import emit_tool_observability_event
from .models import CodeSearchPublicResult, CodeSearchRequest, to_public_result
from .optimization import optimize_query_plan
from .orchestrator import execute_code_search
from .query import build_query_plan

LOGGER = logging.getLogger(__name__)

# Server-controlled result cap — not exposed to callers. The orchestrator
# and compact_hits use this to decide how many hits survive into the final
# response. Inspired by GitHub's own MCP server (search_code) and grep.app
# MCP, neither of which exposes a max_results parameter.
_SERVER_MAX_RESULTS = 50


def _clean_repositories(repositories: list[str] | None) -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(item.strip() for item in (repositories or []) if item and item.strip())
    )
    if len(values) > 25:
        raise ValueError("repositories may contain at most 25 entries.")
    return values


def _validate_request(
    *,
    query: str,
    research_goal: str | None = None,
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
    mode: str = "code",
) -> CodeSearchRequest:
    normalized_query = query.strip()
    normalized_research_goal = " ".join((research_goal or "").split()).strip()
    normalized_research_goal = normalized_research_goal[:500] or normalized_query
    if not normalized_query:
        raise ValueError("query must be a non-blank string.")
    normalized_repo_name = repo_name.strip() if repo_name and repo_name.strip() else None
    normalized_library_name = (
        library_name.strip() if library_name and library_name.strip() else None
    )
    return CodeSearchRequest(
        query=normalized_query,
        research_goal=normalized_research_goal,
        repositories=_clean_repositories(repositories),
        language=language.strip() if language and language.strip() else None,
        path=path.strip() if path and path.strip() else None,
        filename=filename.strip() if filename and filename.strip() else None,
        extension=extension.strip() if extension and extension.strip() else None,
        regexp=regexp,
        deep=deep,
        max_results=_SERVER_MAX_RESULTS,
        repo_name=normalized_repo_name,
        library_name=normalized_library_name,
        topic=topic.strip() if topic and topic.strip() else None,
        mode=mode,
    )


async def code_search(
    query: Annotated[
        str,
        Field(
            description=(
                "What to find in public code or documentation. Supports exact identifiers, "
                "symbols ('FastMCP.tool'), error signatures, or natural language descriptions. "
                "Inline qualifiers are supported: 'repo:owner/repo', 'path:dir/', 'file:name.ext', "
                "'lang:python', 'rev:tag/commit', '-repo:owner/repo', '-file:test.py', "
                "and regex tokens '/pattern/i'."
            ),
            examples=[
                "FastMCP tool registration with annotations",
                "repo:pydantic/pydantic BaseModel field_validator",
                "/def search_code\\([a-z_]+: str\\)/ lang:python",
            ],
        ),
    ] = "",
    research_goal: Annotated[
        str | None,
        Field(
            description=(
                "Optional task context used to guide query rewriting, semantic discovery, "
                "and code-candidate reranking; it is not a literal query term."
            )
        ),
    ] = None,
    repositories: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional GitHub repositories to search, using owner/name strings "
                "such as ['prefecthq/fastmcp']. Up to 25 repositories."
            )
        ),
    ] = None,
    language: Annotated[
        str | None,
        Field(
            description="Optional programming language qualifier, such as Python, Go, TypeScript, Rust, or C++."
        ),
    ] = None,
    path: Annotated[
        str | None,
        Field(
            description=(
                "Optional repository path or glob to narrow matches, such as 'src/tools/' "
                "or '**/mcp/*.py'. File-path tails (e.g. 'src/server.py') automatically "
                "split into directory and filename."
            )
        ),
    ] = None,
    filename: Annotated[
        str | None,
        Field(
            description="Optional exact filename or filename pattern, such as 'server.py' or '*_search.ts'."
        ),
    ] = None,
    extension: Annotated[
        str | None,
        Field(
            description="Optional file extension filter, with or without the leading dot, such as 'py' or 'ts'."
        ),
    ] = None,
    regexp: Annotated[
        bool,
        Field(
            description="Treat the query as a regular expression on providers that support regex search."
        ),
    ] = False,
    deep: Annotated[
        bool,
        Field(
            description="Fetch bounded source windows and perform broader multi-page repository discovery when the initial search needs more evidence."
        ),
    ] = False,
    repo_name: Annotated[
        str | None,
        Field(
            description="Optional repository-name hint for discovering GitHub projects that implement the requested idea."
        ),
    ] = None,
    library_name: Annotated[
        str | None,
        Field(
            description="Optional library or package name to bias documentation and implementation discovery."
        ),
    ] = None,
    topic: Annotated[
        str | None,
        Field(
            description="Optional GitHub topic or ecosystem label used to narrow repository discovery."
        ),
    ] = None,
    mode: Annotated[
        str,
        Field(
            description=(
                "Search mode: 'code' (default, find source code and implementations), "
                "'docs' (documentation and API references), or 'discovery' "
                "(find repositories and projects implementing the requested idea)."
            )
        ),
    ] = "code",
    ctx: Context = CurrentContext(),
) -> CodeSearchPublicResult:
    """Search public code, implementation examples, documentation, and GitHub repositories.

    DSL Cheat Sheet:
    - Identifiers & Symbols: Exact function/class names (e.g. `parse_remote`, `FastMCP.tool`).
    - Inline Qualifiers: `repo:owner/repo`, `path:src/`, `file:app.ts`, `lang:Go`, `rev:v1.2.0`.
    - Negation / Exclusions: `-repo:owner/repo`, `-file:test.py`, `-lang:Java`.
    - Regular Expressions: `/pattern/i` inside query or `regexp=True`.
    - Modes:
      * `code`: Default mode for concrete implementations and code definitions.
      * `docs`: Focuses on API reference documentation and library tutorials.
      * `discovery`: Finds active repositories, stars, and implementations.

    Returns grouped results (Octocode-style): repository → files → text_matches,
    match_lines with exact spans, symbols, sha, and url. Hints and next
    continuations guide agents to fetch exact line anchors via get_content.
    Ranking scores and provider telemetry are omitted.
    """
    tool_call_id = str(uuid.uuid4())
    emit_tool_observability_event(
        LOGGER,
        "code_search",
        "request",
        tool_call_id=tool_call_id,
        query=query,
    )
    started = time.monotonic()
    try:
        request = _validate_request(
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
        )
    except Exception as exc:
        emit_tool_observability_event(
            LOGGER,
            "code_search",
            "error",
            tool_call_id=tool_call_id,
            query=query,
            error_type=type(exc).__name__,
            error_message=str(exc),
            duration_ms=(time.monotonic() - started) * 1000,
        )
        raise

    plan = build_query_plan(
        request.query,
        regexp=request.regexp,
        deep=request.deep,
        repositories=request.repositories,
        language=request.language,
        path=request.path,
        filename=request.filename,
        extension=request.extension,
        max_variants=request.budget.max_query_variants,
        mode=request.mode,
    )
    plan = await optimize_query_plan(plan, request)
    if ctx is not None:
        await ctx.report_progress(progress=5, total=100, message="Planning code search...")
    with create_chain_span(
        "code_search",
        attributes={
            SEARCH_QUERY: request.query[:500],
            "code_search.channels": ",".join(plan.metadata.backend_channels),
        },
    ) as root_span:
        from ...inference.engine import bind_run_context, reset_run_context
        from ...utils.http_client import get_http_client

        ctx_token = bind_run_context(tool_call_id, operation="code_search")
        try:
            if ctx is not None:
                await ctx.report_progress(
                    progress=15, total=100, message="Running selected code-search providers..."
                )
            response = await execute_code_search(
                request,
                plan,
                http_client=await get_http_client(),
            )
        except Exception as exc:
            root_span.set_status(trace.StatusCode.ERROR)
            emit_tool_observability_event(
                LOGGER,
                "code_search",
                "error",
                tool_call_id=tool_call_id,
                query=request.query,
                error_type=type(exc).__name__,
                error_message=str(exc),
                duration_ms=(time.monotonic() - started) * 1000,
                request=request,
                plan=plan,
            )
            raise
        finally:
            reset_run_context(ctx_token)
        root_span.set_attribute("code_search.result_count", len(response.results))
        root_span.set_attribute("code_search.outcome", response.outcome)
        root_span.set_status(
            trace.StatusCode.OK
            if response.outcome in {"ok", "partial", "no_hit"}
            else trace.StatusCode.ERROR
        )

    if ctx is not None:
        await ctx.report_progress(progress=100, total=100, message="Done")
    emit_tool_observability_event(
        LOGGER,
        "code_search",
        "response",
        tool_call_id=tool_call_id,
        query=request.query,
        channels=plan.metadata.backend_channels,
        outcome=response.outcome,
        providers=response.stats.provider_counts,
        output_count=len(response.results),
        duration_ms=(time.monotonic() - started) * 1000,
        request=request,
        plan=plan,
        response=response,
    )
    return to_public_result(response, language=request.language, plan=plan)
