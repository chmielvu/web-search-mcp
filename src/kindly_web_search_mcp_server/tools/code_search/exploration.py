"""Current-main repository snapshot explorer for code_fetch."""

from __future__ import annotations

import base64
import json
import logging
from typing import Annotated, Any, Literal

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import BaseModel, Field

from ...utils.github import normalize_github_repository
from .._helpers import _code_fetch_flight
from .snapshot import (
    GRAPH_WAIT_SECONDS,
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


class CodeFetchGraphStatus(BaseModel):
    status: Literal["pending", "ready", "failed"]
    symbol_count: int = 0
    edge_count: int = 0
    error: str | None = None
    retry_after_seconds: float | None = None


class CodeFetchHit(BaseModel):
    path: str
    start_line: int
    end_line: int
    symbol: dict[str, str] | None = None
    role: str | None = None
    why: list[str] = Field(default_factory=list)
    snippet: str | None = None
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
    graph: CodeFetchGraphStatus | None = None
    has_more: bool = False
    next_cursor: str | None = None
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


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _response_from_query(
    repository: str,
    result: QueryResult,
    *,
    offset: int = 0,
    cursor_params: dict[str, Any] | None = None,
) -> CodeFetchResponse:
    snapshot = result.snapshot
    outcome: Literal["ok", "partial", "error", "stale"] = "ok"
    if snapshot.stale:
        outcome = "stale"
    elif result.error:
        outcome = "error"
    elif result.truncated or snapshot.truncated:
        outcome = "partial"
    warning = snapshot.warning
    graph_status: Literal["pending", "ready", "failed"] = "failed"
    if snapshot.graph_status == "pending":
        graph_status = "pending"
    elif snapshot.graph_status == "ready":
        graph_status = "ready"
    graph = CodeFetchGraphStatus(
        status=graph_status,
        symbol_count=snapshot.graph_symbol_count,
        edge_count=snapshot.graph_edge_count,
        error=snapshot.graph_error,
    )
    if snapshot.graph_status == "pending" and result.intent in {"graph", "map"}:
        pending_warning = (
            "symbol graph still building; symbol results may be file matches — "
            "retry in a few seconds"
        )
        warning = f"{warning}; {pending_warning}" if warning else pending_warning
        graph.retry_after_seconds = GRAPH_WAIT_SECONDS
    if result.intent in {"search", "graph"} and not result.hits and result.error is None:
        no_hit_warning = (
            "No matches found in this repository snapshot. Try broader terms or a "
            "symbol name; repository alone returns the file map."
        )
        warning = f"{warning}; {no_hit_warning}" if warning else no_hit_warning
    next_cursor = None
    if result.intent == "search" and result.has_more and cursor_params is not None:
        next_cursor = _encode_cursor(
            {"v": 1, "offset": offset + len(result.hits), **cursor_params}
        )
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
        next=(
            _next_from_hits(repository, result.hits)
            if result.intent in {"search", "graph"}
            else []
        ),
        graph=graph,
        has_more=result.has_more,
        next_cursor=next_cursor,
        error=result.error,
        warning=warning,
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
    language: Annotated[
        str | None,
        Field(description="Filter hits by language, e.g. python or typescript."),
    ] = None,
    filename: Annotated[
        str | None,
        Field(description="fnmatch filter on file basename, e.g. *.py."),
    ] = None,
    path_glob: Annotated[
        str | None,
        Field(description="fnmatch include filter on repo-relative path."),
    ] = None,
    exclude_glob: Annotated[
        str | None,
        Field(description="fnmatch exclude filter on repo-relative path."),
    ] = None,
    case_sensitive: Annotated[
        bool,
        Field(description="Case-sensitive literal matching."),
    ] = False,
    cursor: Annotated[
        str | None,
        Field(description="Continuation cursor from a previous response's next_cursor."),
    ] = None,
    ctx: Context | None = CurrentContext(),
) -> CodeFetchResponse:
    """Explore a GitHub repository's current main/default branch.

    Materializes a snapshot (TTL from ``CODE_FETCH_SNAPSHOT_TTL_SECONDS``,
    default 300s), then searches, reads, or graphs it. Callers do not pass a
    commit SHA. Every successful response includes ``resolved_commit`` and
    ``cache_age_seconds``.

    query returns matching lines with snippets across the snapshot — follow a
    hit with path (and start_line/end_line) to read whole files. repository
    alone returns a map with the file tree and top symbols; symbol returns
    definitions with callers/callees (waits briefly for the symbol graph after
    a cold clone). Search supports language/filename/glob/case filters and
    cursor pagination via next_cursor/has_more.

    The first call for a repository downloads and indexes it (can take 30-90s);
    later calls within the TTL are fast. ``max_matches`` clamps to [1,100] and
    ``context_lines`` to [0,8]; ``depth`` filters tree depth only. ``map`` is
    returned when no query/path/symbol is given; ``tree`` when path is a
    directory; ``read`` when path is a file. ``truncated`` may reflect the
    snapshot index budget (``snapshot_truncated``) rather than this query's
    results.
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
        # Cursor decode: opaque continuation of the same search on the same
        # snapshot. Any drift (snapshot advanced, params changed) invalidates.
        offset = 0
        cursor_params: dict[str, Any] = {
            "commit": None,
            "query": query,
            "path": path,
            "regexp": regexp,
            "language": language,
            "filename": filename,
            "path_glob": path_glob,
            "exclude_glob": exclude_glob,
            "case_sensitive": case_sensitive,
        }
        if cursor is not None:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
            except Exception:
                return _error_response(
                    normalized_repository, "cursor expired: snapshot advanced or query changed; restart the search without cursor"
                )
            expected = {"v": 1, **{k: v for k, v in cursor_params.items() if k != "commit"}}
            drifted = any(decoded.get(k) != v for k, v in expected.items()) or decoded.get(
                "v"
            ) != 1
            if drifted or decoded.get("commit") != snapshot.resolved_commit:
                return _error_response(
                    normalized_repository, "cursor expired: snapshot advanced or query changed; restart the search without cursor"
                )
            offset = max(0, int(decoded.get("offset", 0)))
        # Symbol and map intents need the graph; wait briefly for the deferred
        # build instead of silently degrading to file matches.
        if symbol or (query is None and path is None):
            await manager.wait_for_graph(snapshot)
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
                    offset=offset,
                    language=language,
                    filename=filename,
                    path_glob=path_glob,
                    exclude_glob=exclude_glob,
                    case_sensitive=case_sensitive,
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
                    offset=offset,
                    language=language,
                    filename=filename,
                    path_glob=path_glob,
                    exclude_glob=exclude_glob,
                    case_sensitive=case_sensitive,
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
                offset=offset,
                language=language,
                filename=filename,
                path_glob=path_glob,
                exclude_glob=exclude_glob,
                case_sensitive=case_sensitive,
            )
    except SnapshotError as exc:
        return _error_response(
            normalized_repository,
            str(exc),
            retry_after_seconds=exc.retry_after_seconds,
        )
    except Exception as exc:  # pragma: no cover - defensive tool boundary
        LOGGER.exception("code_fetch failed for %s", normalized_repository)
        return _error_response(
            normalized_repository,
            f"code_fetch failed: {type(exc).__name__}: {str(exc) or 'no details'}",
        )

    if ctx is not None:
        await ctx.report_progress(progress=100, total=100, message="Snapshot query complete.")
    return _response_from_query(
        normalized_repository,
        result,
        offset=offset,
        cursor_params={**cursor_params, "commit": snapshot.resolved_commit},
    )


__all__ = [
    "CodeFetchHit",
    "CodeFetchNext",
    "CodeFetchRelatedSymbol",
    "CodeFetchResponse",
    "code_fetch",
]
