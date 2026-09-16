"""SnapshotManager class — public lifecycle + thin orchestrator over package helpers.

Search implementations live in :mod:`.query`; SQLite persistence and the
live-cache live in :mod:`.persist`. The manager class delegates to those
module-level helpers (which take the manager as their first argument) instead
of carrying them as private methods.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from pathlib import Path

from ....settings import settings
from ....utils.paths import CACHE_DIR
from .embeddings import _hf_code_embedding  # noqa: F401  (re-exported for callers)
from .fetch import (
    _clone_sparse_repo,
    _collect_files,
    _download_tarball,
    _resolve_main_commit,
    _safe_rmtree,
)
from .graph import _extract_graph
from .models import (
    GRAPH_WAIT_SECONDS,
    LOGGER as _LOGGER,  # noqa: F401  (re-exported for callers)
    MAX_CONTENT_CHARS,
    QueryResult,
    Snapshot,
    SnapshotError,
    SnapshotHit,
    _repo_key,
)
from .persist import (
    _deferred_graph_build,
    _ensure_schema,
    _persist,
    _remember,
    _restore_persisted_snapshot,
)
from .query import (
    _architecture,
    _graph_hits,
    _hit_from_file,  # noqa: F401  (re-exported for callers)
    _search_hits,
    _search_hits_async,
    _symbol_at,  # noqa: F401  (re-exported for callers)
)
from .scan import _list_tree, _merge_hits, _read_text

__all__ = ["SnapshotManager", "get_snapshot_manager", "reset_snapshot_manager_for_tests"]


class SnapshotManager:
    """Process-local snapshot cache keyed by owner/name, TTL 5 minutes."""

    def __init__(
        self,
        *,
        db_path: str | None = None,
        worktree_root: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path or settings.code_fetch_snapshot_sqlite_path)
        self.worktree_root = Path(worktree_root or (CACHE_DIR / "code_fetch_worktrees"))
        self._lock = threading.Lock()
        self._live: dict[str, Snapshot] = {}
        _ensure_schema(self)

    def live_snapshot(self, repository: str) -> Snapshot | None:
        snap = self._live.get(repository)
        if snap is None or snap.expired():
            return None
        return snap

    def build_from_directory(
        self,
        repository: str,
        branch: str,
        resolved_commit: str,
        source_root: Path,
        *,
        truncated: bool = False,
        defer_graph: bool = False,
        requested_ref: str = "",
    ) -> Snapshot:
        """Index an already-materialized directory. Used by tests and after extract."""

        self.worktree_root.mkdir(parents=True, exist_ok=True)
        dest = self.worktree_root / repository.replace("/", "__") / resolved_commit[:12]
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        file_count, truncated_index, skipped_binary, records = _collect_files(source_root, dest)
        truncated = truncated or truncated_index
        if defer_graph:
            symbols, edges = [], []
        else:
            symbols, edges = _extract_graph(records)
        now = time.monotonic()
        snapshot = Snapshot(
            repository=repository,
            branch=branch,
            resolved_commit=resolved_commit,
            root=dest,
            created_at=now,
            file_count=file_count,
            truncated=truncated,
            warning=(
                f"{skipped_binary} binary file(s) skipped from index"
                if skipped_binary > 0
                else None
            ),
            requested_ref=requested_ref,
        )
        if not defer_graph:
            snapshot.graph_status = "ready"
            snapshot.graph_symbol_count = len(symbols)
            snapshot.graph_edge_count = len(edges)
        _persist(self, snapshot, records, symbols, edges)
        _remember(self, snapshot, key=_repo_key(snapshot))
        return snapshot

    async def wait_for_graph(self, snapshot: Snapshot, budget: float | None = None) -> None:
        """Bounded wait for a pending deferred graph build (symbol/map intents)."""
        if snapshot.graph_status != "pending":
            return
        if snapshot.graph_task is None:
            return
        try:
            done, _pending = await asyncio.wait(
                {snapshot.graph_task}, timeout=budget or GRAPH_WAIT_SECONDS
            )
        except Exception:
            pass  # Task failure is recorded in graph_status by _deferred_graph_build.

    async def ensure(self, repository: str, *, ref: str | None = None) -> Snapshot:
        key = f"{repository}@{ref}" if ref else repository
        live = self.live_snapshot(key)
        if live is not None:
            return live
        persisted = _restore_persisted_snapshot(self, repository, key)
        if persisted is not None:
            return persisted
        return await self.refresh(repository, ref=ref)

    async def refresh(self, repository: str, *, ref: str | None = None) -> Snapshot:
        key = f"{repository}@{ref}" if ref else repository
        previous = self._live.get(key)
        try:
            branch, sha = await _resolve_main_commit(repository, ref=ref)
            if previous is not None and previous.resolved_commit == sha:
                previous.created_at = time.monotonic()
                previous.stale = False
                previous.warning = None
                _remember(self, previous, key=key)
                return previous
            root = await asyncio.to_thread(_clone_sparse_repo, repository, branch, sha, ref)
            if root is None:
                root = await _download_tarball(repository, sha)
            snapshot = await asyncio.to_thread(
                self.build_from_directory,
                repository,
                branch,
                sha,
                root,
                defer_graph=True,
                requested_ref=ref or "",
            )
            _safe_rmtree(root)
            _remember(self, snapshot, key=key)
            # TreeSitter graph (symbols/edges) is expensive (30-50% of cold time)
            # and only needed for symbol/callers queries. Defer it so the
            # snapshot is usable for search/read/tree immediately.
            try:
                snapshot.graph_task = asyncio.create_task(_deferred_graph_build(self, snapshot))
            except RuntimeError:
                pass  # No loop (tests) - graph built on demand or not needed
            return snapshot
        except SnapshotError as exc:
            if previous is not None:
                previous.stale = True
                previous.warning = str(exc)
                return previous
            raise

    def query(
        self,
        snapshot: Snapshot,
        *,
        query: str | None,
        path: str | None,
        symbol: str | None,
        regexp: bool,
        max_matches: int,
        context_lines: int,
        start_line: int | None = None,
        end_line: int | None = None,
        depth: int | None = None,
        offset: int = 0,
        language: str | None = None,
        filename: str | None = None,
        path_glob: str | None = None,
        exclude_glob: str | None = None,
        case_sensitive: bool = False,
    ) -> QueryResult:
        normalized_query = (query or "").strip()
        normalized_path = (path or "").strip().strip("/")
        normalized_symbol = (symbol or "").strip()
        limit = max(1, min(max_matches, 100))
        context = max(0, min(context_lines, 8))

        # A line window is only meaningful for a direct file read. When the
        # caller also passes query/symbol (or omits path), start_line/end_line
        # were previously silently ignored, producing results the caller did
        # not ask for. Reject the combination explicitly instead.
        window_requested = start_line is not None or end_line is not None
        if window_requested and not (
            normalized_path and not normalized_query and not normalized_symbol
        ):
            return QueryResult(
                snapshot=snapshot,
                intent="read",
                error=(
                    "start_line/end_line only apply when reading a file: "
                    "pass path and omit query/symbol"
                ),
            )

        if normalized_path and not normalized_query and not normalized_symbol:
            target = snapshot.root / normalized_path
            if target.is_file():
                raw_text = _read_text(target)
                lines = raw_text.splitlines()
                total_lines = len(lines)
                if start_line is not None or end_line is not None:
                    sl = max(1, start_line or 1)
                    el = min(total_lines, end_line or total_lines)
                    sl = min(sl, total_lines or 1)
                    el = max(sl, el)
                    selected_lines = lines[sl - 1 : el]
                    sliced_text = "\n".join(selected_lines)
                    # 0 MAX_CONTENT_CHARS means unlimited - return full content
                    content_out = (
                        sliced_text if MAX_CONTENT_CHARS <= 0 else sliced_text[:MAX_CONTENT_CHARS]
                    )
                    is_truncated = (
                        False if MAX_CONTENT_CHARS <= 0 else len(sliced_text) > MAX_CONTENT_CHARS
                    )
                    return QueryResult(
                        snapshot=snapshot,
                        intent="read",
                        content=content_out,
                        truncated=is_truncated,
                        hits=[
                            SnapshotHit(
                                path=normalized_path,
                                start_line=sl,
                                end_line=el,
                                snippet=None,
                                why=["read:window"],
                                confidence=1.0,
                            )
                        ],
                    )
                raw_content_out = (
                    raw_text if MAX_CONTENT_CHARS <= 0 else raw_text[:MAX_CONTENT_CHARS]
                )
                raw_truncated = (
                    False if MAX_CONTENT_CHARS <= 0 else len(raw_text) > MAX_CONTENT_CHARS
                )
                return QueryResult(
                    snapshot=snapshot,
                    intent="read",
                    content=raw_content_out,
                    truncated=raw_truncated,
                    hits=[
                        SnapshotHit(
                            path=normalized_path,
                            start_line=1,
                            end_line=max(1, total_lines),
                            snippet=None,
                            why=["read"],
                            confidence=1.0,
                        )
                    ],
                )
            entries = _list_tree(snapshot.root, normalized_path, limit=2_000, depth=depth)
            if not entries and not target.exists():
                return QueryResult(
                    snapshot=snapshot,
                    intent="read",
                    error="path was not found in the snapshot",
                )
            return QueryResult(
                snapshot=snapshot,
                intent="tree",
                tree=entries,
                truncated=len(entries) >= 2_000,
            )

        if normalized_symbol and not normalized_query:
            hits = _graph_hits(
                self, snapshot, normalized_symbol, limit=limit, context_lines=context
            )
            return QueryResult(
                snapshot=snapshot,
                intent="graph",
                hits=hits,
                truncated=len(hits) >= limit,
            )

        if not normalized_query and not normalized_symbol:
            return QueryResult(
                snapshot=snapshot,
                intent="map",
                architecture=_architecture(self, snapshot),
            )

        hits, truncated, has_more = _search_hits(
            self,
            snapshot,
            normalized_query,
            path_prefix=normalized_path or None,
            regexp=regexp,
            limit=limit,
            context_lines=context,
            offset=offset,
            language=language,
            filename=filename,
            path_glob=path_glob,
            exclude_glob=exclude_glob,
            case_sensitive=case_sensitive,
        )
        if normalized_symbol:
            graph_hits = _graph_hits(
                self, snapshot, normalized_symbol, limit=limit, context_lines=context
            )
            hits = _merge_hits(graph_hits, hits, limit)
        return QueryResult(
            snapshot=snapshot,
            intent="search",
            hits=hits,
            truncated=truncated or len(hits) >= limit,
            has_more=has_more,
        )

    async def query_async(
        self,
        snapshot: Snapshot,
        *,
        query: str | None,
        path: str | None,
        symbol: str | None,
        regexp: bool,
        max_matches: int,
        context_lines: int,
        start_line: int | None = None,
        end_line: int | None = None,
        depth: int | None = None,
        offset: int = 0,
        language: str | None = None,
        filename: str | None = None,
        path_glob: str | None = None,
        exclude_glob: str | None = None,
        case_sensitive: bool = False,
    ) -> QueryResult:
        """Async counterpart to query() with semantic fallback.

        For read/tree/graph/map intents, delegates to sync query(). For search
        intent with 0 hits, attempts semantic fallback via the shared ml/
        embedding client (fastembed-snowflake, Arctic 384-dim), merging via
        deduplication (RRF-like). Falls back gracefully if the service is
        unavailable.
        """
        # Reuse sync validation for window, read/tree/graph fast paths
        # Call sync query first to handle non-search intents
        base = self.query(
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
        # If not a search miss, return as-is
        if base.intent != "search" or base.hits or base.truncated or base.error is not None:
            return base
        # Search miss — attempt async semantic. Semantic candidates are
        # whole-file embeddings: filters, case, and offsets have no faithful
        # meaning there, so only plain first-page searches may fall back.
        normalized_query = (query or "").strip()
        semantic_unsupported = bool(
            language or filename or path_glob or exclude_glob or case_sensitive or offset
        )
        if not normalized_query or regexp or semantic_unsupported:
            return base
        normalized_path = (path or "").strip().strip("/")
        limit = max(1, min(max_matches, 100))
        context = max(0, min(context_lines, 8))
        try:
            sem_hits, sem_trunc = await _search_hits_async(
                self,
                snapshot,
                normalized_query,
                path_prefix=normalized_path or None,
                regexp=False,
                limit=limit,
                context_lines=context,
            )
            if sem_hits:
                # If symbol also requested, merge graph hits as sync query does
                hits = sem_hits
                if (symbol or "").strip():
                    graph_hits = _graph_hits(
                        self, snapshot, (symbol or "").strip(), limit=limit, context_lines=context
                    )
                    hits = _merge_hits(graph_hits, hits, limit)
                return QueryResult(
                    snapshot=snapshot,
                    intent="search",
                    hits=hits,
                    truncated=sem_trunc or len(hits) >= limit,
                )
        except Exception as exc:
            _LOGGER.debug("semantic fallback failed: %s", exc)
        return base


_MANAGER: SnapshotManager | None = None


def get_snapshot_manager() -> SnapshotManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = SnapshotManager()
    return _MANAGER


def reset_snapshot_manager_for_tests(manager: SnapshotManager | None = None) -> None:
    global _MANAGER
    _MANAGER = manager
