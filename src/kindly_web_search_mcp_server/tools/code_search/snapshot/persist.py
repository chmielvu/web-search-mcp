"""SQLite persistence and live-cache helpers for SnapshotManager.

Module-level functions operate on a :class:`SnapshotManager` instance passed in
as the first argument. Splitting these off :class:`SnapshotManager` keeps the
manager class focused on public lifecycle methods while preserving identical
SQL, TTLs, logging, and error handling.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import SnapshotManager


import contextlib

from ..tree_sitter_evidence import language_for_path
from .graph import _extract_graph
from .models import (
    _SKIP_DIRS,
    _SKIP_SUFFIXES,
    LOGGER,
    MAX_FILE_BYTES,
    MAX_LIVE_SNAPSHOTS,
    TTL_SECONDS,
    Snapshot,
    _repo_key,
)


def _connect(manager: SnapshotManager) -> sqlite3.Connection:
    manager.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(manager.db_path), timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA busy_timeout=30000;")
    return con


def _ensure_schema(manager: SnapshotManager) -> None:
    with manager._lock:
        con = _connect(manager)
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


def _remember(manager: SnapshotManager, snapshot: Snapshot, key: str | None = None) -> None:
    manager._live[key or snapshot.repository] = snapshot
    if len(manager._live) <= MAX_LIVE_SNAPSHOTS:
        return
    oldest_key = min(manager._live.keys(), key=lambda k: manager._live[k].created_at)
    target_key = key or snapshot.repository
    if oldest_key != target_key:
        manager._live.pop(oldest_key, None)


def _persist(
    manager: SnapshotManager,
    snapshot: Snapshot,
    records: list[tuple[str, str | None, int, str]],
    symbols: list[tuple[str, str, str, int, int]],
    edges: list[tuple[str, str, int | None, str, str, str | None, int | None, float]],
) -> None:
    repo_key = _repo_key(snapshot)
    with manager._lock:
        con = _connect(manager)
        try:
            with con:
                con.execute("DELETE FROM files WHERE repository = ?", (repo_key,))
                con.execute("DELETE FROM symbols WHERE repository = ?", (repo_key,))
                con.execute("DELETE FROM edges WHERE repository = ?", (repo_key,))
                with contextlib.suppress(sqlite3.OperationalError):
                    con.execute(
                        "DELETE FROM files_fts WHERE repository = ?",
                        (repo_key,),
                    )
                con.execute(
                    """
                    INSERT OR REPLACE INTO snapshots
                    (repository, branch, resolved_commit, root, created_at, file_count, truncated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        repo_key,
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
                        (repo_key, path, language, size, content)
                        for path, language, size, content in records
                    ],
                )
                con.executemany(
                    "INSERT INTO symbols (repository, path, name, kind, start_line, end_line) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (repo_key, path, name, kind, start_line, end_line)
                        for path, name, kind, start_line, end_line in symbols
                    ],
                )
                con.executemany(
                    "INSERT INTO edges (repository, source_name, source_path, source_line, relation, "
                    "target_name, target_path, target_line, confidence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            repo_key,
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
                with contextlib.suppress(sqlite3.OperationalError):
                    con.executemany(
                        "INSERT INTO files_fts (repository, path, content) VALUES (?, ?, ?)",
                        [(repo_key, path, content) for path, _language, _size, content in records],
                    )
        finally:
            con.close()


def _restore_persisted_snapshot(
    manager: SnapshotManager, repository: str, key: str
) -> Snapshot | None:
    """Restore a recent materialized snapshot whose worktree still exists."""
    with manager._lock:
        con = _connect(manager)
        try:
            row = con.execute(
                "SELECT branch, resolved_commit, root, created_at, file_count, truncated "
                "FROM snapshots WHERE repository = ?",
                (key,),
            ).fetchone()
            symbol_count = int(
                con.execute(
                    "SELECT COUNT(*) FROM symbols WHERE repository = ?",
                    (key,),
                ).fetchone()[0]
            )
            edge_count = int(
                con.execute(
                    "SELECT COUNT(*) FROM edges WHERE repository = ?",
                    (key,),
                ).fetchone()[0]
            )
        finally:
            con.close()
    if row is None:
        return None
    root = Path(str(row["root"]))
    if not root.is_dir():
        return None
    created_at = float(row["created_at"])
    age = time.monotonic() - created_at
    # A monotonic timestamp from before a reboot is in the future. Do not
    # resurrect it as a fresh snapshot after the monotonic clock resets.
    if age < 0 or age >= TTL_SECONDS:
        return None
    snapshot = Snapshot(
        repository=repository,
        branch=str(row["branch"]),
        resolved_commit=str(row["resolved_commit"]),
        root=root,
        created_at=created_at,
        file_count=int(row["file_count"]),
        truncated=bool(row["truncated"]),
        requested_ref=key[len(repository) + 1 :] if key.startswith(f"{repository}@") else "",
    )
    if symbol_count > 0:
        snapshot.graph_status = "ready"
        snapshot.graph_symbol_count = symbol_count
        snapshot.graph_edge_count = edge_count
    else:
        # The deferred build re-reads the worktree, so it works on restore.
        # No loop (some test paths) — status stays pending.
        with contextlib.suppress(RuntimeError):
            snapshot.graph_task = asyncio.create_task(_deferred_graph_build(manager, snapshot))
    _remember(manager, snapshot, key=key)
    return snapshot


async def _deferred_graph_build(manager: SnapshotManager, snapshot: Snapshot) -> None:
    """Build TreeSitter graph in background after snapshot is usable."""
    try:
        repo_key = _repo_key(snapshot)
        # Re-collect file records from the worktree (already materialized)
        records: list[tuple[str, str | None, int, str]] = []
        for path in sorted(snapshot.root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(snapshot.root)
            if any(part in _SKIP_DIRS for part in relative.parts):
                continue
            if path.suffix.casefold() in _SKIP_SUFFIXES:
                continue
            try:
                data = path.read_bytes()
                if len(data) > MAX_FILE_BYTES:
                    continue
                if b"\0" in data[:1024]:
                    continue
                text = data.decode("utf-8", errors="replace")
                records.append(
                    (
                        relative.as_posix(),
                        language_for_path(relative.as_posix()),
                        len(data),
                        text,
                    )
                )
            except Exception:
                continue
        symbols, edges = await asyncio.to_thread(_extract_graph, records)
        with manager._lock:
            con = _connect(manager)
            try:
                with con:
                    con.execute("DELETE FROM symbols WHERE repository = ?", (repo_key,))
                    con.execute("DELETE FROM edges WHERE repository = ?", (repo_key,))
                    con.executemany(
                        "INSERT INTO symbols (repository, path, name, kind, start_line, end_line) VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            (repo_key, path, name, kind, start_line, end_line)
                            for path, name, kind, start_line, end_line in symbols
                        ],
                    )
                    con.executemany(
                        "INSERT INTO edges (repository, source_name, source_path, source_line, relation, target_name, target_path, target_line, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            (
                                repo_key,
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
            finally:
                con.close()
        snapshot.graph_status = "ready"
        snapshot.graph_symbol_count = len(symbols)
        snapshot.graph_edge_count = len(edges)
        LOGGER.info(
            "Deferred graph build completed for %s: %d symbols, %d edges",
            snapshot.repository,
            len(symbols),
            len(edges),
        )
    except Exception as exc:
        snapshot.graph_status = "failed"
        snapshot.graph_error = str(exc)[:500]
        LOGGER.warning("Deferred graph build failed for %s: %s", snapshot.repository, exc)
