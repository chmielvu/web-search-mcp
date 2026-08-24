"""Current-main repository snapshot explorer for code_fetch."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import BaseModel, Field

from ...utils.github import normalize_github_repository
from .._helpers import _code_fetch_flight
from .snapshot import (
    QueryResult,
    SnapshotError,
    SnapshotHit,
    get_snapshot_manager,
)

LOGGER = logging.getLogger(__name__)


class CodeFetchRelatedSymbol(BaseModel):
    name: str
    path: str
    line: int | None = None


class CodeFetchHit(BaseModel):
    path: str
    start_line: int
    end_line: int
    symbol: dict[str, str] | None = None
    role: str | None = None
    why: list[str] = Field(default_factory=list)
    snippet: str = ""
    callers: list[CodeFetchRelatedSymbol] = Field(default_factory=list)
    callees: list[CodeFetchRelatedSymbol] = Field(default_factory=list)
    confidence: float | None = None


class CodeFetchNext(BaseModel):
    repository: str
    path: str | None = None
    query: str | None = None
    symbol: str | None = None


class CodeFetchResponse(BaseModel):
    outcome: Literal["ok", "partial", "error", "stale"]
    repository: str
    branch: str | None = None
    resolved_commit: str | None = None
    cache_age_seconds: int | None = None
    expires_in_seconds: int | None = None
    stale: bool = False
    truncated: bool = False
    search_truncated: bool = False
    snapshot_truncated: bool = False
    intent: str | None = None
    hits: list[CodeFetchHit] = Field(default_factory=list)
    tree: list[str] = Field(default_factory=list)
    content: str | None = None
    map: dict[str, Any] | None = None
    next: list[CodeFetchNext] = Field(default_factory=list)
    error: str | None = None
    warning: str | None = None
    retry_after_seconds: float | None = None


def _normalize_repository(repository: str) -> str:
    return normalize_github_repository(repository)


def _hit_model(hit: SnapshotHit) -> CodeFetchHit:
    symbol = None
    if hit.symbol_name:
        symbol = {"name": hit.symbol_name, "kind": hit.symbol_kind or "symbol"}
    return CodeFetchHit(
        path=hit.path,
        start_line=hit.start_line,
        end_line=hit.end_line,
        symbol=symbol,
        role=hit.role,
        why=list(hit.why),
        snippet=hit.snippet,
        callers=[
            CodeFetchRelatedSymbol(name=item.name, path=item.path, line=item.line)
            for item in hit.callers
        ],
        callees=[
            CodeFetchRelatedSymbol(name=item.name, path=item.path, line=item.line)
            for item in hit.callees
        ],
        confidence=hit.confidence,
    )


def _next_from_hits(repository: str, hits: list[SnapshotHit]) -> list[CodeFetchNext]:
    seen: set[str] = set()
    items: list[CodeFetchNext] = []
    for hit in hits[:3]:
        if hit.path in seen:
            continue
        seen.add(hit.path)
        items.append(
            CodeFetchNext(
                repository=repository,
                path=hit.path,
                symbol=hit.symbol_name,
            )
        )
    return items


def _response_from_query(repository: str, result: QueryResult) -> CodeFetchResponse:
    snapshot = result.snapshot
    outcome: Literal["ok", "partial", "error", "stale"] = "ok"
    if snapshot.stale:
        outcome = "stale"
    elif result.error:
        outcome = "error"
    elif result.truncated or snapshot.truncated:
        outcome = "partial"
    return CodeFetchResponse(
        outcome=outcome,
        repository=repository,
        branch=snapshot.branch,
        resolved_commit=snapshot.resolved_commit,
        cache_age_seconds=snapshot.age_seconds(),
        expires_in_seconds=snapshot.expires_in_seconds(),
        stale=snapshot.stale,
        truncated=result.truncated or snapshot.truncated,
        search_truncated=result.truncated,
        snapshot_truncated=snapshot.truncated,
        intent=result.intent,
        hits=[_hit_model(hit) for hit in result.hits],
        tree=list(result.tree),
        content=result.content,
        map=result.architecture,
        next=_next_from_hits(repository, result.hits),
        error=result.error,
        warning=snapshot.warning,
    )


def _error_response(
    repository: str,
    message: str,
    *,
    retry_after_seconds: float | None = None,
) -> CodeFetchResponse:
    return CodeFetchResponse(
        outcome="error",
        repository=repository,
        error=message[:500],
        retry_after_seconds=retry_after_seconds,
        stale=False,
        truncated=False,
    )


async def code_fetch(
    repository: Annotated[
        str,
        Field(description="GitHub owner/name repository, for example prefecthq/fastmcp."),
    ],
    query: Annotated[
        str | None,
        Field(description="Identifier, regex, or natural-language query over current main."),
    ] = None,
    path: Annotated[
        str | None,
        Field(description="Optional file or directory scope inside the snapshot."),
    ] = None,
    symbol: Annotated[
        str | None,
        Field(description="Optional symbol for callers/callees/impact."),
    ] = None,
    ref: Annotated[
        str | None,
        Field(description="Optional git revision (branch, tag, or commit SHA)."),
    ] = None,
    regexp: Annotated[
        bool,
        Field(description="Treat query as a regular expression."),
    ] = False,
    max_matches: Annotated[int, Field(description="Maximum returned hits.")] = 25,
    context_lines: Annotated[int, Field(description="Context lines around each match.")] = 3,
    start_line: Annotated[
        int | None,
        Field(description="Optional 1-based start line when reading a file (requires path without query/symbol)."),
    ] = None,
    end_line: Annotated[
        int | None,
        Field(description="Optional 1-based end line when reading a file (requires path without query/symbol)."),
    ] = None,
    depth: Annotated[
        int | None,
        Field(description="Optional max directory tree depth (e.g. 1 for top-level only)."),
    ] = None,
    ctx: Context | None = CurrentContext(),
) -> CodeFetchResponse:
    """Explore a GitHub repository's current main/default branch.

    Materializes a five-minute snapshot, then searches, reads, or graphs it.
    Callers do not pass a commit SHA. Every successful response includes
    ``resolved_commit`` and ``cache_age_seconds``.

    The first call for a repository downloads and indexes it (can take 30-90s);
    later calls within five minutes are fast. To read a line window, pass
    ``path`` plus ``start_line``/``end_line`` and omit ``query``/``symbol``.
    To search inside one file or directory, pass ``query`` (or ``symbol``)
    together with ``path``. ``truncated`` may reflect the snapshot index
    budget (``snapshot_truncated``) rather than this query's results.
    """
    try:
        normalized_repository = _normalize_repository(repository)
    except ValueError as exc:
        return _error_response(repository.strip(), str(exc))

    if ctx is not None:
        await ctx.report_progress(progress=10, total=100, message="Opening main-branch snapshot...")

    manager = get_snapshot_manager()
    flight_key = _code_fetch_flight.make_key(f"{normalized_repository}@{ref}" if ref else normalized_repository)

    async def _open_snapshot():
        return await manager.ensure(normalized_repository, ref=ref)

    try:
        # Initiator (cold clone+index) can take 25-70s+; bound it just under the
        # catalog timeout (180s) so waiters (55s) get a clear error before the
        # outer tool timeout. 160s leaves 20s for query.
        snapshot = await _code_fetch_flight.do(
            flight_key, _open_snapshot, timeout_seconds=55.0, initiator_timeout_seconds=160.0
        )
        # Use async query with HF semantic fallback (st-codesearch-distilroberta-base)
        # when FTS+literal yield 0 hits and HF_TOKEN present. Falls back to
        # sync query for read/tree/graph and when semantic unavailable.
        if hasattr(manager, "query_async"):
            try:
                result = await manager.query_async(
                    snapshot,
                    query=query,
                    path=path,
                    symbol=symbol,
                    regexp=regexp,
                    max_matches=max_matches,
                    context_lines=context_lines,
                    start_line=start_line,
                    end_line=end_line,
                    depth=depth,
                )
            except Exception as exc:
                LOGGER.debug("query_async failed, falling back to sync query: %s", exc)
                result = manager.query(
                    snapshot,
                    query=query,
                    path=path,
                    symbol=symbol,
                    regexp=regexp,
                    max_matches=max_matches,
                    context_lines=context_lines,
                    start_line=start_line,
                    end_line=end_line,
                    depth=depth,
                )
        else:
            result = manager.query(
                snapshot,
                query=query,
                path=path,
                symbol=symbol,
                regexp=regexp,
                max_matches=max_matches,
                context_lines=context_lines,
                start_line=start_line,
                end_line=end_line,
                depth=depth,
            )
    except SnapshotError as exc:
        return _error_response(
            normalized_repository,
            str(exc),
            retry_after_seconds=exc.retry_after_seconds,
        )
    except Exception as exc:  # pragma: no cover - defensive tool boundary
        LOGGER.exception("code_fetch failed for %s", normalized_repository)
        return _error_response(normalized_repository, f"code_fetch failed: {type(exc).__name__}")

    if ctx is not None:
        await ctx.report_progress(progress=100, total=100, message="Snapshot query complete.")
    return _response_from_query(normalized_repository, result)


__all__ = [
    "CodeFetchHit",
    "CodeFetchNext",
    "CodeFetchRelatedSymbol",
    "CodeFetchResponse",
    "code_fetch",
]
