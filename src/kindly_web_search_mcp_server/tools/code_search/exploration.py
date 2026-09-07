"""Current-main repository snapshot explorer for code_fetch."""

from __future__ import annotations

import base64
import json
import logging
from typing import Annotated, Any, Literal

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import BaseModel, Field

from ...errors import raise_tool_error
from ...utils.github import normalize_github_repository
from ...utils.http_client import get_http_client
from .._helpers import _code_fetch_flight
from .github import _token
from .hydration import hydrate_sources
from .models import CodeSearchHit
from .snapshot import (
    GRAPH_WAIT_SECONDS,
    MAX_CONTENT_CHARS,
    QueryResult,
    SnapshotError,
    SnapshotHit,
    _resolve_main_commit,
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


class CodeFetchFile(BaseModel):
    """One whole file read in a multi-path bulk read."""

    path: str
    content: str
    start_line: int
    end_line: int
    truncated: bool = False


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
    files: list[CodeFetchFile] = Field(default_factory=list)
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
    missing_paths: list[str] | None = None


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
        next_cursor = _encode_cursor({"v": 1, "offset": offset + len(result.hits), **cursor_params})
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
            _next_from_hits(repository, result.hits) if result.intent in {"search", "graph"} else []
        ),
        graph=graph,
        has_more=result.has_more,
        next_cursor=next_cursor,
        error=result.error,
        warning=warning,
    )



def _is_single_file_read_candidate(
    *,
    query: str | None,
    path: str | None,
    symbol: str | None,
    depth: int | None,
    language: str | None,
    filename: str | None,
    path_glob: str | None,
    exclude_glob: str | None,
    cursor: str | None,
) -> bool:
    return (
        bool((path or "").strip().strip("/"))
        and not (query or "").strip()
        and not (symbol or "").strip()
        and cursor is None
        and depth is None
        and language is None
        and filename is None
        and path_glob is None
        and exclude_glob is None
    )


async def _try_fast_lane_github_file(
    repository: str,
    path: str,
    *,
    ref: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> CodeFetchResponse | None:
    branch: str | None = ref
    sha: str | None = None
    try:
        branch, sha = await _resolve_main_commit(repository, ref=ref)
    except SnapshotError:
        branch = ref
        sha = None
    except Exception:
        branch = ref
        sha = None

    try:
        client = await get_http_client()
        token = _token()
        hit = CodeSearchHit(
            repository=repository,
            path=path,
            provider="github",
            commit_oid=sha or ref,
        )
        max_chars = MAX_CONTENT_CHARS if MAX_CONTENT_CHARS > 0 else 5_000_000
        sources, diagnostics = await hydrate_sources(
            [hit],
            http_client=client,
            token=token,
            max_files=1,
            max_chars_per_file=max_chars,
        )
    except Exception:
        LOGGER.debug("code_fetch fast-lane hydrate failed", exc_info=True)
        return None

    source = sources.get((repository.casefold(), path.replace("\\", "/").casefold()))
    if source is None or not source.text:
        return None

    if source.text.lstrip().startswith("["):
        try:
            parsed = json.loads(source.text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return None

    was_truncated = (
        any(diag.failure_kind == "budget" for diag in (diagnostics or []) if diag)
        and MAX_CONTENT_CHARS <= 0
    )
    lines = source.text.splitlines()
    total_lines = len(lines)
    if start_line is not None or end_line is not None:
        sl = max(1, start_line or 1)
        el = min(total_lines, end_line or total_lines)
        sl = min(sl, total_lines or 1)
        el = max(sl, el)
        content_out = "\n".join(lines[sl - 1 : el])
        why = ["read:window"]
        hit_start, hit_end = sl, el
    else:
        content_out = source.text
        why = ["read"]
        hit_start, hit_end = 1, max(1, total_lines)

    LOGGER.info(
        "code_fetch fast-lane hit",
        extra={"repository": repository, "path": path, "ref": ref},
    )
    warning = (
        "file exceeded the fast-lane 5,000,000-char cap; re-read via repository+path "
        "after snapshot warm-up for full content"
        if was_truncated
        else None
    )
    return CodeFetchResponse(
        outcome="ok",
        repository=repository,
        branch=branch,
        resolved_commit=sha,
        cache_age_seconds=0,
        expires_in_seconds=300,
        stale=False,
        truncated=was_truncated,
        search_truncated=False,
        snapshot_truncated=False,
        intent="read",
        hits=[
            CodeFetchHit(
                path=path,
                start_line=hit_start,
                end_line=hit_end,
                why=why,
                confidence=1.0,
            )
        ],
        tree=[],
        content=content_out,
        map=None,
        next=[],
        graph=None,
        has_more=False,
        next_cursor=None,
        error=None,
        warning=warning,
    )


async def _try_fast_lane_github_files(
    repository: str,
    paths: list[str],
    *,
    ref: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> CodeFetchResponse | None:
    """Bulk variant of the fast lane: one commit resolution, one hydrate call."""

    branch: str | None = ref
    sha: str | None = None
    try:
        branch, sha = await _resolve_main_commit(repository, ref=ref)
    except SnapshotError:
        branch = ref
        sha = None
    except Exception:
        branch = ref
        sha = None

    try:
        client = await get_http_client()
        token = _token()
        hits = [
            CodeSearchHit(
                repository=repository,
                path=path,
                provider="github",
                commit_oid=sha or ref,
            )
            for path in paths
        ]
        max_chars = MAX_CONTENT_CHARS if MAX_CONTENT_CHARS > 0 else 5_000_000
        sources, diagnostics = await hydrate_sources(
            hits,
            http_client=client,
            token=token,
            max_files=len(paths),
            max_chars_per_file=max_chars,
        )
    except Exception:
        LOGGER.debug("code_fetch bulk fast-lane hydrate failed", exc_info=True)
        return None

    files: list[CodeFetchFile] = []
    missing: list[str] = []
    any_truncated = False
    for path in paths:
        source = sources.get((repository.casefold(), path.replace("\\", "/").casefold()))
        if source is None or not source.text:
            missing.append(path)
            continue
        file_truncated = (
            any(
                diag.failure_kind == "budget"
                and isinstance(diag.query, str)
                and diag.query == repository
                for diag in (diagnostics or [])
            )
            and MAX_CONTENT_CHARS <= 0
        )
        if file_truncated:
            any_truncated = True
        lines = source.text.splitlines()
        total_lines = max(1, len(lines))
        sl = max(1, start_line or 1)
        el = min(total_lines, end_line or total_lines)
        sl = min(sl, total_lines)
        el = max(sl, el)
        files.append(
            CodeFetchFile(
                path=path,
                content="\n".join(lines[sl - 1 : el]),
                start_line=sl,
                end_line=el,
                truncated=file_truncated,
            )
        )

    if not files:
        return None
    warning_parts: list[str] = []
    if missing:
        warning_parts.append(f"paths not found: {', '.join(missing)}")
    if any_truncated:
        warning_parts.append(
            "one or more files exceeded the fast-lane 5,000,000-char cap; "
            "re-read via repository+path after snapshot warm-up for full content"
        )
    return CodeFetchResponse(
        outcome="partial" if (any_truncated or missing) else "ok",
        repository=repository,
        branch=branch,
        resolved_commit=sha,
        cache_age_seconds=0,
        expires_in_seconds=300,
        stale=False,
        truncated=any_truncated,
        search_truncated=False,
        snapshot_truncated=False,
        intent="read",
        hits=[],
        files=files,
        tree=[],
        content=None,
        map=None,
        next=[],
        graph=None,
        has_more=False,
        next_cursor=None,
        error=None,
        warning="; ".join(warning_parts) if warning_parts else None,
        missing_paths=missing or None,
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
    paths: Annotated[
        list[str] | None,
        Field(
            description=(
                "1-5 repository-relative file paths to read in one call. Requires path "
                "unset; incompatible with query, symbol, cursor, depth, language, "
                "filename, path_glob, exclude_glob. start_line/end_line apply to every file."
            )
        ),
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
        Field(
            description="Optional 1-based start line when reading a file (requires path without query/symbol)."
        ),
    ] = None,
    end_line: Annotated[
        int | None,
        Field(
            description="Optional 1-based end line when reading a file (requires path without query/symbol)."
        ),
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

    GitHub repository exploration tool: prefer query searches and symbol lookups
    over full-file reads; for one-off non-repo URLs use fetch; for cross-repo
    discovery use code_search. Materializes a snapshot (TTL from
    ``CODE_FETCH_SNAPSHOT_TTL_SECONDS``, default 300s), then searches, reads, or
    graphs it. Callers do not pass a commit SHA. Every successful response
    includes ``resolved_commit`` and ``cache_age_seconds``.

    query returns matching lines with snippets across the snapshot — follow a
    hit with path (and start_line/end_line) to read whole files, or pass up to 5
    paths at once for a bulk read (response.files[]). repository alone returns a
    map with the file tree and top symbols; symbol returns definitions with
    callers/callees (waits briefly for the symbol graph after a cold clone).
    Search supports language/filename/glob/case filters and cursor pagination
    via next_cursor/has_more.

    Approximate token cost: query search ~50-300 tokens/hit; line-window read
    ~100-500; whole-file read can exceed 10k; repository alone returns the map.
    Prefer search over full-file reads.

    The first call for a repository downloads and indexes it (can take 30-90s);
    later calls within the TTL are fast. ``max_matches`` clamps to [1,100] and
    ``context_lines`` to [0,8]; ``depth`` filters tree depth only. ``map`` is
    returned when no query/path/symbol/paths is given; ``tree`` when path is a
    directory; ``read`` when path is a file; ``files`` when paths is a list.
    ``truncated`` may reflect the snapshot index budget (``snapshot_truncated``)
    rather than this query's results.
    """
    try:
        normalized_repository = _normalize_repository(repository)
    except ValueError as exc:
        raise_tool_error(ValueError(str(exc)), provider="code_fetch")

    if paths is not None:
        cleaned_paths = [
            item.strip().strip("/")
            for item in paths
            if isinstance(item, str) and item.strip().strip("/")
        ]
        deduped_paths = list(dict.fromkeys(cleaned_paths))
        conflicts = [
            name
            for name, value in (
                ("query", query),
                ("symbol", symbol),
                ("cursor", cursor),
                ("depth", depth),
                ("language", language),
                ("filename", filename),
                ("path_glob", path_glob),
                ("exclude_glob", exclude_glob),
                ("case_sensitive", True if case_sensitive else None),
            )
            if value not in (None, False)
        ]
        if path is not None:
            conflicts.append("path")
        if not 1 <= len(deduped_paths) <= 5:
            raise_tool_error(
                ValueError(
                    "paths requires 1-5 non-blank repository-relative file paths after dedup; got "
                    + str(len(deduped_paths))
                ),
                provider="code_fetch",
            )
        if conflicts:
            raise_tool_error(
                ValueError(
                    "paths is incompatible with: "
                    + ", ".join(conflicts)
                    + "; call again with only paths"
                ),
                provider="code_fetch",
            )
        if len(deduped_paths) == 1:
            path = deduped_paths[0]
            paths = None
        else:
            key = f"{normalized_repository}@{ref}" if ref else normalized_repository
            if get_snapshot_manager().live_snapshot(key) is None:
                fast = await _try_fast_lane_github_files(
                    normalized_repository,
                    deduped_paths,
                    ref=ref,
                    start_line=start_line,
                    end_line=end_line,
                )
                if fast is not None:
                    if ctx is not None:
                        await ctx.report_progress(
                            progress=100, total=100, message="GitHub bulk file read complete."
                        )
                    return fast
            manager = get_snapshot_manager()
            snapshot = await manager.ensure(normalized_repository, ref=ref)
            files: list[CodeFetchFile] = []
            missing: list[str] = []
            any_truncated = False
            for item in deduped_paths:
                file_result = manager.query(
                    snapshot,
                    query=None,
                    path=item,
                    symbol=None,
                    regexp=False,
                    max_matches=1,
                    context_lines=0,
                    start_line=start_line,
                    end_line=end_line,
                )
                if file_result.intent == "read" and file_result.hits:
                    hit0 = file_result.hits[0]
                    files.append(
                        CodeFetchFile(
                            path=item,
                            content=file_result.content or "",
                            start_line=hit0.start_line,
                            end_line=hit0.end_line,
                            truncated=file_result.truncated,
                        )
                    )
                    if file_result.truncated:
                        any_truncated = True
                else:
                    missing.append(item)
            if not files:
                raise_tool_error(
                    ValueError("paths not found: " + ", ".join(missing)),
                    provider="code_fetch",
                )
            warning_parts: list[str] = []
            if missing:
                warning_parts.append(f"paths not found: {', '.join(missing)}")
            if any_truncated:
                warning_parts.append(
                    "one or more files exceeded the fast-lane cap; "
                    "re-read via repository+path after snapshot warm-up for full content"
                )
            if ctx is not None:
                await ctx.report_progress(
                    progress=100, total=100, message="Snapshot bulk read complete."
                )
            return CodeFetchResponse(
                outcome="partial" if (any_truncated or missing) else "ok",
                repository=normalized_repository,
                branch=snapshot.branch,
                resolved_commit=snapshot.resolved_commit,
                cache_age_seconds=snapshot.age_seconds(),
                expires_in_seconds=snapshot.expires_in_seconds(),
                stale=snapshot.stale,
                truncated=any_truncated,
                search_truncated=False,
                snapshot_truncated=False,
                intent="read",
                hits=[],
                files=files,
                tree=[],
                content=None,
                map=None,
                graph=None,
                has_more=False,
                next_cursor=None,
                error=None,
                warning="; ".join(warning_parts) if warning_parts else None,
                missing_paths=missing or None,
            )

    if _is_single_file_read_candidate(
        query=query,
        path=path,
        symbol=symbol,
        depth=depth,
        language=language,
        filename=filename,
        path_glob=path_glob,
        exclude_glob=exclude_glob,
        cursor=cursor,
    ):
        key = f"{normalized_repository}@{ref}" if ref else normalized_repository
        if get_snapshot_manager().live_snapshot(key) is None:
            fast = await _try_fast_lane_github_file(
                normalized_repository,
                (path or "").strip().strip("/"),
                ref=ref,
                start_line=start_line,
                end_line=end_line,
            )
            if fast is not None:
                if ctx is not None:
                    await ctx.report_progress(
                        progress=100, total=100, message="GitHub file read complete."
                    )
                return fast

    if ctx is not None:
        await ctx.report_progress(progress=10, total=100, message="Opening main-branch snapshot...")

    manager = get_snapshot_manager()
    flight_key = _code_fetch_flight.make_key(
        f"{normalized_repository}@{ref}" if ref else normalized_repository
    )

    async def _open_snapshot():
        return await manager.ensure(normalized_repository, ref=ref)

    try:
        # Initiator (cold clone+index) can take 25-70s+; bound it just under the
        # catalog timeout (180s) so waiters (55s) get a clear error before the
        # outer tool timeout. 160s leaves 20s for query.
        snapshot = await _code_fetch_flight.do(
            flight_key, _open_snapshot, timeout_seconds=160.0, initiator_timeout_seconds=160.0
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
                raise_tool_error(
                    ValueError(
                        "cursor expired: snapshot advanced or query changed; restart the search without cursor"
                    ),
                    provider="code_fetch",
                )
            expected = {"v": 1, **{k: v for k, v in cursor_params.items() if k != "commit"}}
            drifted = any(decoded.get(k) != v for k, v in expected.items()) or decoded.get("v") != 1
            if drifted or decoded.get("commit") != snapshot.resolved_commit:
                raise_tool_error(
                    ValueError(
                        "cursor expired: snapshot advanced or query changed; restart the search without cursor"
                    ),
                    provider="code_fetch",
                )
            offset = max(0, int(decoded.get("offset", 0)))
        # Symbol and map intents need the graph; wait briefly for the deferred
        # build instead of silently degrading to file matches.
        if symbol or (query is None and path is None):
            await manager.wait_for_graph(snapshot)
        # Use async query with semantic fallback via the shared ml/ embedding
        # client (fastembed-snowflake, Arctic 384-dim) when FTS+literal yield
        # 0 hits. Falls back to sync query for read/tree/graph.
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
        raise_tool_error(ValueError(str(exc)), provider="code_fetch")
    except Exception as exc:  # pragma: no cover - defensive tool boundary
        LOGGER.exception("code_fetch failed for %s", normalized_repository)
        raise_tool_error(
            ValueError(
                f"code_fetch failed: {type(exc).__name__}: {str(exc) or 'no details'}"
            ),
            provider="code_fetch",
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
    "CodeFetchFile",
    "CodeFetchHit",
    "CodeFetchNext",
    "CodeFetchRelatedSymbol",
    "CodeFetchResponse",
    "code_fetch",
]
