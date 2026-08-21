"""Main-branch repository snapshots for code_fetch.

Materializes the current default branch into a bounded worktree, keeps a
five-minute in-memory snapshot plus SQLite FTS/graph indexes, and answers
search/read/graph queries against that snapshot.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
import sqlite3
import tarfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import httpx

from ...settings import settings
from ...utils.http_client import get_http_client
from ...utils.paths import CACHE_DIR
from .github import _GITHUB_API_URL, _headers, _retry_after, _token
from .tree_sitter_evidence import classify_source, language_for_path

LOGGER = logging.getLogger(__name__)

TTL_SECONDS = 300
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
MAX_EXTRACTED_BYTES = 120 * 1024 * 1024
MAX_FILES = 4_000
MAX_FILE_BYTES = 1_000_000
MAX_LIVE_SNAPSHOTS = 4
MAX_SNIPPET_CHARS = 1_200

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "out",
    "target",
    "vendor",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".next",
    ".nuxt",
    "coverage",
}
_SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tgz",
    ".whl",
    ".so",
    ".dll",
    ".dylib",
    ".bin",
    ".exe",
    ".wasm",
    ".lock",
}


@dataclass(slots=True)
class RelatedSymbol:
    name: str
    path: str
    line: int | None = None


@dataclass(slots=True)
class SnapshotHit:
    path: str
    start_line: int
    end_line: int
    symbol_name: str | None = None
    symbol_kind: str | None = None
    role: str | None = None
    why: list[str] = field(default_factory=list)
    snippet: str = ""
    callers: list[RelatedSymbol] = field(default_factory=list)
    callees: list[RelatedSymbol] = field(default_factory=list)
    confidence: float = 0.5


@dataclass(slots=True)
class Snapshot:
    repository: str
    branch: str
    resolved_commit: str
    root: Path
    created_at: float
    file_count: int
    truncated: bool = False
    stale: bool = False
    warning: str | None = None

    def age_seconds(self, now: float | None = None) -> int:
        return max(0, int((now or time.monotonic()) - self.created_at))

    def expires_in_seconds(self, now: float | None = None) -> int:
        return max(0, TTL_SECONDS - self.age_seconds(now))

    def expired(self, now: float | None = None) -> bool:
        return self.age_seconds(now) >= TTL_SECONDS


@dataclass(slots=True)
class QueryResult:
    snapshot: Snapshot
    intent: str
    hits: list[SnapshotHit] = field(default_factory=list)
    tree: list[str] = field(default_factory=list)
    content: str | None = None
    architecture: dict[str, Any] | None = None
    truncated: bool = False
    error: str | None = None


class SnapshotError(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


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
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.db_path), timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA busy_timeout=5000;")
        return con

    def _ensure_schema(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                with con:
                    con.execute(
                        """
                        CREATE TABLE IF NOT EXISTS snapshots (
                            repository TEXT PRIMARY KEY,
                            branch TEXT NOT NULL,
                            resolved_commit TEXT NOT NULL,
                            root TEXT NOT NULL,
                            created_at REAL NOT NULL,
                            file_count INTEGER NOT NULL,
                            truncated INTEGER NOT NULL
                        )
                        """
                    )
                    con.execute(
                        """
                        CREATE TABLE IF NOT EXISTS files (
                            repository TEXT NOT NULL,
                            path TEXT NOT NULL,
                            language TEXT,
                            size INTEGER NOT NULL,
                            content TEXT,
                            PRIMARY KEY (repository, path)
                        )
                        """
                    )
                    con.execute(
                        """
                        CREATE TABLE IF NOT EXISTS symbols (
                            repository TEXT NOT NULL,
                            path TEXT NOT NULL,
                            name TEXT NOT NULL,
                            kind TEXT,
                            start_line INTEGER NOT NULL,
                            end_line INTEGER NOT NULL
                        )
                        """
                    )
                    con.execute(
                        """
                        CREATE TABLE IF NOT EXISTS edges (
                            repository TEXT NOT NULL,
                            source_name TEXT NOT NULL,
                            source_path TEXT NOT NULL,
                            source_line INTEGER,
                            relation TEXT NOT NULL,
                            target_name TEXT NOT NULL,
                            target_path TEXT,
                            target_line INTEGER,
                            confidence REAL NOT NULL
                        )
                        """
                    )
                    try:
                        con.execute(
                            "CREATE VIRTUAL TABLE IF NOT EXISTS files_fts "
                            "USING fts5(repository UNINDEXED, path, content)"
                        )
                    except sqlite3.OperationalError as exc:
                        LOGGER.warning("code_fetch FTS5 unavailable: %s", exc)
            finally:
                con.close()

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
    ) -> Snapshot:
        """Index an already-materialized directory. Used by tests and after extract."""

        self.worktree_root.mkdir(parents=True, exist_ok=True)
        dest = self.worktree_root / repository.replace("/", "__") / resolved_commit[:12]
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        file_count, truncated_index, records = _collect_files(source_root, dest)
        truncated = truncated or truncated_index
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
        )
        self._persist(snapshot, records, symbols, edges)
        self._remember(snapshot)
        return snapshot

    def _persist(
        self,
        snapshot: Snapshot,
        records: list[tuple[str, str | None, int, str]],
        symbols: list[tuple[str, str, str, int, int]],
        edges: list[tuple[str, str, int | None, str, str, str | None, int | None, float]],
    ) -> None:
        with self._lock:
            con = self._connect()
            try:
                with con:
                    con.execute("DELETE FROM files WHERE repository = ?", (snapshot.repository,))
                    con.execute("DELETE FROM symbols WHERE repository = ?", (snapshot.repository,))
                    con.execute("DELETE FROM edges WHERE repository = ?", (snapshot.repository,))
                    try:
                        con.execute(
                            "DELETE FROM files_fts WHERE repository = ?",
                            (snapshot.repository,),
                        )
                    except sqlite3.OperationalError:
                        pass
                    con.execute(
                        """
                        INSERT OR REPLACE INTO snapshots
                        (repository, branch, resolved_commit, root, created_at, file_count, truncated)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot.repository,
                            snapshot.branch,
                            snapshot.resolved_commit,
                            str(snapshot.root),
                            snapshot.created_at,
                            snapshot.file_count,
                            int(snapshot.truncated),
                        ),
                    )
                    con.executemany(
                        "INSERT INTO files (repository, path, language, size, content) VALUES (?, ?, ?, ?, ?)",
                        [
                            (snapshot.repository, path, language, size, content)
                            for path, language, size, content in records
                        ],
                    )
                    con.executemany(
                        "INSERT INTO symbols (repository, path, name, kind, start_line, end_line) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            (snapshot.repository, path, name, kind, start_line, end_line)
                            for path, name, kind, start_line, end_line in symbols
                        ],
                    )
                    con.executemany(
                        "INSERT INTO edges (repository, source_name, source_path, source_line, relation, "
                        "target_name, target_path, target_line, confidence) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            (
                                snapshot.repository,
                                source_name,
                                source_path,
                                source_line,
                                relation,
                                target_name,
                                target_path,
                                target_line,
                                confidence,
                            )
                            for (
                                source_name,
                                source_path,
                                source_line,
                                relation,
                                target_name,
                                target_path,
                                target_line,
                                confidence,
                            ) in edges
                        ],
                    )
                    try:
                        con.executemany(
                            "INSERT INTO files_fts (repository, path, content) VALUES (?, ?, ?)",
                            [
                                (snapshot.repository, path, content)
                                for path, _language, _size, content in records
                            ],
                        )
                    except sqlite3.OperationalError:
                        pass
            finally:
                con.close()

    def _remember(self, snapshot: Snapshot) -> None:
        self._live[snapshot.repository] = snapshot
        if len(self._live) <= MAX_LIVE_SNAPSHOTS:
            return
        oldest = min(self._live.values(), key=lambda item: item.created_at)
        if oldest.repository != snapshot.repository:
            self._live.pop(oldest.repository, None)

    async def ensure(self, repository: str) -> Snapshot:
        live = self.live_snapshot(repository)
        if live is not None:
            return live
        return await self.refresh(repository)

    async def refresh(self, repository: str) -> Snapshot:
        previous = self._live.get(repository)
        try:
            branch, sha = await _resolve_main_commit(repository)
            if previous is not None and previous.resolved_commit == sha:
                previous.created_at = time.monotonic()
                previous.stale = False
                previous.warning = None
                self._remember(previous)
                return previous
            root = await _download_tarball(repository, sha)
            snapshot = await asyncio.to_thread(
                self.build_from_directory,
                repository,
                branch,
                sha,
                root,
            )
            shutil.rmtree(root, ignore_errors=True)
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
    ) -> QueryResult:
        normalized_query = (query or "").strip()
        normalized_path = (path or "").strip().strip("/")
        normalized_symbol = (symbol or "").strip()
        limit = max(1, min(max_matches, 100))
        context = max(0, min(context_lines, 8))

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
                    return QueryResult(
                        snapshot=snapshot,
                        intent="read",
                        content=sliced_text[:200_000],
                        truncated=len(sliced_text) > 200_000,
                        hits=[
                            SnapshotHit(
                                path=normalized_path,
                                start_line=sl,
                                end_line=el,
                                snippet=sliced_text[:MAX_SNIPPET_CHARS],
                                why=["read:window"],
                                confidence=1.0,
                            )
                        ],
                    )
                return QueryResult(
                    snapshot=snapshot,
                    intent="read",
                    content=raw_text[:200_000],
                    truncated=len(raw_text) > 200_000,
                    hits=[
                        SnapshotHit(
                            path=normalized_path,
                            start_line=1,
                            end_line=max(1, total_lines),
                            snippet=raw_text[:MAX_SNIPPET_CHARS],
                            why=["read"],
                            confidence=1.0,
                        )
                    ],
                )
            entries = _list_tree(snapshot.root, normalized_path, limit=2_000)
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
            hits = self._graph_hits(snapshot, normalized_symbol, limit=limit, context_lines=context)
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
                architecture=self._architecture(snapshot),
            )

        hits, truncated = self._search_hits(
            snapshot,
            normalized_query,
            path_prefix=normalized_path or None,
            regexp=regexp,
            limit=limit,
            context_lines=context,
        )
        if normalized_symbol:
            graph_hits = self._graph_hits(
                snapshot, normalized_symbol, limit=limit, context_lines=context
            )
            hits = _merge_hits(hits, graph_hits, limit)
        return QueryResult(
            snapshot=snapshot,
            intent="search",
            hits=hits,
            truncated=truncated or len(hits) >= limit,
        )

    def _search_hits(
        self,
        snapshot: Snapshot,
        query: str,
        *,
        path_prefix: str | None,
        regexp: bool,
        limit: int,
        context_lines: int,
    ) -> tuple[list[SnapshotHit], bool]:
        if regexp:
            try:
                pattern = re.compile(query)
            except re.error as exc:
                raise SnapshotError(f"invalid regular expression: {exc}") from exc
            return _scan_regex(
                snapshot.root,
                pattern,
                path_prefix=path_prefix,
                limit=limit,
                context_lines=context_lines,
            )
        hits: list[SnapshotHit] = []
        fts_hits = self._fts_hits(snapshot.repository, query, path_prefix=path_prefix, limit=limit)
        if fts_hits:
            hits = [
                self._hit_from_file(snapshot, path, query, context_lines=context_lines, why=["fts"])
                for path in fts_hits
            ]
        scanned, scan_truncated = _scan_literal(
            snapshot.root,
            query,
            path_prefix=path_prefix,
            limit=limit,
            context_lines=context_lines,
        )
        hits = _merge_hits(hits, scanned, limit)
        for hit in hits:
            hit.callers, hit.callees = self._neighbors(
                snapshot.repository, hit.symbol_name or query, hit.path
            )
            if hit.symbol_name is None:
                symbol = self._symbol_at(snapshot.repository, hit.path, hit.start_line)
                if symbol is not None:
                    hit.symbol_name, hit.symbol_kind, hit.role = symbol
        return hits[:limit], scan_truncated or len(hits) >= limit

    def _fts_hits(
        self,
        repository: str,
        query: str,
        *,
        path_prefix: str | None,
        limit: int,
    ) -> list[str]:
        terms = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query) if len(token) > 1]
        if not terms:
            return []
        match = " AND ".join(f'"{term}"' for term in terms[:8])
        sql = "SELECT path FROM files_fts WHERE repository = ? AND files_fts MATCH ? LIMIT ?"
        args: list[Any] = [repository, match, limit]
        if path_prefix:
            sql = (
                "SELECT path FROM files_fts WHERE repository = ? AND files_fts MATCH ? "
                "AND path LIKE ? LIMIT ?"
            )
            args = [repository, match, f"{path_prefix}%", limit]
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(sql, args).fetchall()
            except sqlite3.OperationalError:
                return []
            finally:
                con.close()
        return [str(row["path"]) for row in rows]

    def _graph_hits(
        self,
        snapshot: Snapshot,
        symbol: str,
        *,
        limit: int,
        context_lines: int,
    ) -> list[SnapshotHit]:
        with self._lock:
            con = self._connect()
            try:
                defs = con.execute(
                    "SELECT path, name, kind, start_line, end_line FROM symbols "
                    "WHERE repository = ? AND name = ? LIMIT ?",
                    (snapshot.repository, symbol, limit),
                ).fetchall()
            finally:
                con.close()
        hits: list[SnapshotHit] = []
        for row in defs:
            path = str(row["path"])
            start_line = int(row["start_line"])
            end_line = int(row["end_line"])
            callers, callees = self._neighbors(snapshot.repository, symbol, path)
            hits.append(
                SnapshotHit(
                    path=path,
                    start_line=start_line,
                    end_line=end_line,
                    symbol_name=str(row["name"]),
                    symbol_kind=str(row["kind"] or "definition"),
                    role="definition",
                    why=["graph:definition"],
                    snippet=_snippet(snapshot.root / path, start_line, end_line, context_lines),
                    callers=callers,
                    callees=callees,
                    confidence=0.9,
                )
            )
        if hits:
            return hits[:limit]
        return [
            self._hit_from_file(
                snapshot,
                path,
                symbol,
                context_lines=context_lines,
                why=["graph:name"],
            )
            for path in self._fts_hits(snapshot.repository, symbol, path_prefix=None, limit=limit)
        ]

    def _neighbors(
        self,
        repository: str,
        name: str | None,
        path: str,
    ) -> tuple[list[RelatedSymbol], list[RelatedSymbol]]:
        if not name:
            return [], []
        with self._lock:
            con = self._connect()
            try:
                callers = con.execute(
                    "SELECT source_name, source_path, source_line FROM edges "
                    "WHERE repository = ? AND target_name = ? LIMIT 3",
                    (repository, name),
                ).fetchall()
                callees = con.execute(
                    "SELECT target_name, target_path, target_line FROM edges "
                    "WHERE repository = ? AND source_name = ? AND source_path = ? LIMIT 3",
                    (repository, name, path),
                ).fetchall()
            finally:
                con.close()
        return (
            [
                RelatedSymbol(str(row["source_name"]), str(row["source_path"]), row["source_line"])
                for row in callers
            ],
            [
                RelatedSymbol(
                    str(row["target_name"]),
                    str(row["target_path"] or ""),
                    row["target_line"],
                )
                for row in callees
            ],
        )

    def _symbol_at(
        self, repository: str, path: str, line: int
    ) -> tuple[str, str | None, str] | None:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT name, kind FROM symbols WHERE repository = ? AND path = ? "
                    "AND start_line <= ? AND end_line >= ? LIMIT 1",
                    (repository, path, line, line),
                ).fetchone()
            finally:
                con.close()
        if row is None:
            return None
        return str(row["name"]), row["kind"], "definition"

    def _hit_from_file(
        self,
        snapshot: Snapshot,
        path: str,
        query: str,
        *,
        context_lines: int,
        why: list[str],
    ) -> SnapshotHit:
        file_path = snapshot.root / path
        start_line, end_line, snippet = _first_match_snippet(file_path, query, context_lines)
        return SnapshotHit(
            path=path,
            start_line=start_line,
            end_line=end_line,
            why=why,
            snippet=snippet,
            confidence=0.7,
        )

    def _architecture(self, snapshot: Snapshot) -> dict[str, Any]:
        with self._lock:
            con = self._connect()
            try:
                languages = con.execute(
                    "SELECT language, COUNT(*) AS n FROM files WHERE repository = ? "
                    "GROUP BY language ORDER BY n DESC LIMIT 12",
                    (snapshot.repository,),
                ).fetchall()
                top_symbols = con.execute(
                    "SELECT name, kind, path, start_line FROM symbols WHERE repository = ? "
                    "ORDER BY start_line LIMIT 20",
                    (snapshot.repository,),
                ).fetchall()
            finally:
                con.close()
        return {
            "file_count": snapshot.file_count,
            "languages": {str(row["language"] or "unknown"): int(row["n"]) for row in languages},
            "symbols": [
                {
                    "name": str(row["name"]),
                    "kind": row["kind"],
                    "path": str(row["path"]),
                    "line": int(row["start_line"]),
                }
                for row in top_symbols
            ],
        }


_MANAGER: SnapshotManager | None = None


def get_snapshot_manager() -> SnapshotManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = SnapshotManager()
    return _MANAGER


def reset_snapshot_manager_for_tests(manager: SnapshotManager | None = None) -> None:
    global _MANAGER
    _MANAGER = manager


async def _resolve_main_commit(repository: str) -> tuple[str, str]:
    token = _token()
    client = await get_http_client()
    owner, repo = repository.split("/", 1)
    repo_url = f"{_GITHUB_API_URL}/repos/{quote(owner)}/{quote(repo)}"
    try:
        response = await client.get(
            repo_url, headers=_headers(token), timeout=settings.search_retrieve_budget_seconds
        )
    except httpx.HTTPError as exc:
        raise SnapshotError(f"GitHub repository lookup failed: {exc}") from exc
    if response.status_code != 200:
        raise SnapshotError(
            f"GitHub repository lookup returned HTTP {response.status_code}",
            retry_after_seconds=_retry_after(response),
        )
    payload = response.json() if response.content else {}
    default_branch = payload.get("default_branch") if isinstance(payload, dict) else None
    branch = default_branch if isinstance(default_branch, str) and default_branch else "main"
    try:
        commit_response = await client.get(
            f"{repo_url}/commits/{quote(branch)}",
            headers=_headers(token),
            timeout=settings.search_retrieve_budget_seconds,
        )
    except httpx.HTTPError as exc:
        raise SnapshotError(f"GitHub commit lookup failed: {exc}") from exc
    if (
        commit_response.status_code == 404
        and branch != "main"
    ):
        branch = "main"
        commit_response = await client.get(
            f"{repo_url}/commits/{quote(branch)}",
            headers=_headers(token),
            timeout=settings.search_retrieve_budget_seconds,
        )
    if commit_response.status_code != 200:
        raise SnapshotError(
            f"GitHub commit lookup returned HTTP {commit_response.status_code}",
            retry_after_seconds=_retry_after(commit_response),
        )
    commit_payload = commit_response.json() if commit_response.content else {}
    sha = commit_payload.get("sha") if isinstance(commit_payload, dict) else None
    if not isinstance(sha, str) or len(sha) < 7:
        raise SnapshotError("GitHub commit lookup omitted sha")
    return branch, sha


async def _download_tarball(repository: str, sha: str) -> Path:
    token = _token()
    client = await get_http_client()
    owner, repo = repository.split("/", 1)
    url = f"{_GITHUB_API_URL}/repos/{quote(owner)}/{quote(repo)}/tarball/{quote(sha)}"
    try:
        response = await client.get(
            url,
            headers=_headers(token),
            timeout=settings.search_retrieve_budget_seconds,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise SnapshotError(f"GitHub tarball download failed: {exc}") from exc
    if response.status_code != 200:
        raise SnapshotError(
            f"GitHub tarball returned HTTP {response.status_code}",
            retry_after_seconds=_retry_after(response),
        )
    data = response.content
    if len(data) > MAX_ARCHIVE_BYTES:
        raise SnapshotError("repository archive exceeds the code_fetch size budget")
    dest = Path(CACHE_DIR) / "code_fetch_extracts" / f"{owner}__{repo}__{sha[:12]}"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_extract_tarball, data, dest)
    return dest


def _extract_tarball(data: bytes, dest: Path) -> None:
    extracted = 0
    files = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            name = member.name.replace("\\", "/")
            parts = [part for part in name.split("/") if part not in ("", ".")]
            if not parts or any(part == ".." for part in parts):
                continue
            relative = Path(*parts[1:]) if len(parts) > 1 else Path()
            if not str(relative) or any(part in _SKIP_DIRS for part in relative.parts):
                continue
            target = dest / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            files += 1
            if files > MAX_FILES:
                break
            extracted += int(member.size or 0)
            if extracted > MAX_EXTRACTED_BYTES:
                break
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as handle:
                handle.write(source.read(MAX_FILE_BYTES + 1))


def _collect_files(
    source_root: Path,
    dest: Path,
) -> tuple[int, bool, list[tuple[str, str | None, int, str]]]:
    records: list[tuple[str, str | None, int, str]] = []
    truncated = False
    copied = 0
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_root)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.casefold() in _SKIP_SUFFIXES:
            continue
        if copied >= MAX_FILES:
            truncated = True
            break
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            truncated = True
            continue
        if b"\0" in data[:1024]:
            continue
        text = data.decode("utf-8", errors="replace")
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append((relative.as_posix(), language_for_path(relative.as_posix()), len(data), text))
        copied += 1
    return copied, truncated, records


def _extract_graph(
    records: Iterable[tuple[str, str | None, int, str]],
) -> tuple[
    list[tuple[str, str, str, int, int]],
    list[tuple[str, str, int | None, str, str, str | None, int | None, float]],
]:
    symbols: list[tuple[str, str, str, int, int]] = []
    calls: list[tuple[str, str, int, str]] = []
    definitions: dict[str, list[tuple[str, int]]] = {}
    for path, language, _size, content in records:
        classification = classify_source(content, language=language, path=path)
        scope_stack: list[tuple[str, int, int]] = []
        for item in classification.evidence:
            name = item.name
            if not name:
                continue
            while scope_stack and scope_stack[-1][2] < item.start_line:
                scope_stack.pop()
            if item.role == "definition":
                symbols.append((path, name, item.kind, item.start_line, item.end_line))
                definitions.setdefault(name, []).append((path, item.start_line))
                scope_stack.append((name, item.start_line, item.end_line))
            elif item.role == "callsite" and scope_stack:
                enclosing = scope_stack[-1][0]
                calls.append((enclosing, path, item.start_line, name))
    edges: list[tuple[str, str, int | None, str, str, str | None, int | None, float]] = []
    for source_name, source_path, source_line, target_name in calls:
        targets = definitions.get(target_name, [])
        if len(targets) == 1:
            target_path, target_line = targets[0]
            edges.append(
                (source_name, source_path, source_line, "calls", target_name, target_path, target_line, 0.8)
            )
        elif len(targets) > 1:
            same_file = [t for t in targets if t[0] == source_path]
            if same_file:
                target_path, target_line = same_file[0]
                confidence = 0.7
            else:
                target_path, target_line = targets[0]
                confidence = 0.5
            edges.append(
                (source_name, source_path, source_line, "calls", target_name, target_path, target_line, confidence)
            )
        elif not targets:
            edges.append((source_name, source_path, source_line, "calls", target_name, None, None, 0.3))
    return symbols, edges


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _list_tree(root: Path, prefix: str, *, limit: int) -> list[str]:
    base = root / prefix if prefix else root
    if not base.exists():
        return []
    entries: list[str] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        entries.append(relative.as_posix())
        if len(entries) >= limit:
            break
    return entries


def _scan_literal(
    root: Path,
    query: str,
    *,
    path_prefix: str | None,
    limit: int,
    context_lines: int,
) -> tuple[list[SnapshotHit], bool]:
    needle = query.casefold()
    hits: list[SnapshotHit] = []
    for path in _iter_files(root, path_prefix):
        lines = _read_text(path).splitlines()
        relative = path.relative_to(root).as_posix()
        for index, line in enumerate(lines, start=1):
            if needle not in line.casefold():
                continue
            start = max(1, index - context_lines)
            end = min(len(lines), index + context_lines)
            hits.append(
                SnapshotHit(
                    path=relative,
                    start_line=index,
                    end_line=index,
                    why=["literal"],
                    snippet="\n".join(lines[start - 1 : end])[:MAX_SNIPPET_CHARS],
                    confidence=0.85,
                )
            )
            if len(hits) >= limit:
                return hits, True
    return hits, False


def _scan_regex(
    root: Path,
    pattern: re.Pattern[str],
    *,
    path_prefix: str | None,
    limit: int,
    context_lines: int,
) -> tuple[list[SnapshotHit], bool]:
    hits: list[SnapshotHit] = []
    for path in _iter_files(root, path_prefix):
        lines = _read_text(path).splitlines()
        relative = path.relative_to(root).as_posix()
        for index, line in enumerate(lines, start=1):
            if not pattern.search(line):
                continue
            start = max(1, index - context_lines)
            end = min(len(lines), index + context_lines)
            hits.append(
                SnapshotHit(
                    path=relative,
                    start_line=index,
                    end_line=index,
                    why=["regex"],
                    snippet="\n".join(lines[start - 1 : end])[:MAX_SNIPPET_CHARS],
                    confidence=0.8,
                )
            )
            if len(hits) >= limit:
                return hits, True
    return hits, False


def _iter_files(root: Path, path_prefix: str | None) -> Iterable[Path]:
    base = root / path_prefix if path_prefix else root
    if base.is_file():
        yield base
        return
    if not base.exists():
        return
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.casefold() in _SKIP_SUFFIXES:
            continue
        yield path


def _first_match_snippet(path: Path, query: str, context_lines: int) -> tuple[int, int, str]:
    if not path.is_file():
        return 1, 1, ""
    lines = _read_text(path).splitlines()
    needle = query.casefold()
    for index, line in enumerate(lines, start=1):
        if needle in line.casefold():
            start = max(1, index - context_lines)
            end = min(len(lines), index + context_lines)
            return index, index, "\n".join(lines[start - 1 : end])[:MAX_SNIPPET_CHARS]
    tokens = [
        t.casefold()
        for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query)
        if len(t) > 1
    ]
    if tokens:
        for index, line in enumerate(lines, start=1):
            line_cf = line.casefold()
            if any(token in line_cf for token in tokens):
                start = max(1, index - context_lines)
                end = min(len(lines), index + context_lines)
                return index, index, "\n".join(lines[start - 1 : end])[:MAX_SNIPPET_CHARS]
    snippet = "\n".join(lines[: max(1, context_lines * 2 + 1)])[:MAX_SNIPPET_CHARS]
    return 1, min(len(lines) or 1, context_lines * 2 + 1), snippet


def _snippet(path: Path, start_line: int, end_line: int, context_lines: int) -> str:
    if not path.is_file():
        return ""
    lines = _read_text(path).splitlines()
    start = max(1, start_line - context_lines)
    end = min(len(lines), end_line + context_lines)
    return "\n".join(lines[start - 1 : end])[:MAX_SNIPPET_CHARS]


def _merge_hits(left: list[SnapshotHit], right: list[SnapshotHit], limit: int) -> list[SnapshotHit]:
    seen: set[tuple[str, int]] = set()
    merged: list[SnapshotHit] = []
    for hit in [*left, *right]:
        key = (hit.path, hit.start_line)
        if key in seen:
            continue
        seen.add(key)
        merged.append(hit)
        if len(merged) >= limit:
            break
    return merged


__all__ = [
    "QueryResult",
    "RelatedSymbol",
    "Snapshot",
    "SnapshotError",
    "SnapshotHit",
    "SnapshotManager",
    "TTL_SECONDS",
    "get_snapshot_manager",
    "reset_snapshot_manager_for_tests",
]
