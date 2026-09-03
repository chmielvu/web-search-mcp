"""Main-branch repository snapshots for code_fetch.

Materializes the current default branch into a bounded worktree, keeps a
five-minute in-memory snapshot plus SQLite FTS/graph indexes, and answers
search/read/graph queries against that snapshot.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import shutil
import subprocess
import sqlite3
import tarfile
import threading
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
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

# HF Inference for semantic code search — normal embeddings via Hugging Face InferenceClient
# Model: flax-sentence-embeddings/st-codesearch-distilroberta-base (768-dim, DistilRoBERTa, CodeSearchNet)
# Verified via web_search + HF docs: https://huggingface.co/docs/inference-providers/tasks/feature-extraction
# and https://huggingface.co/docs/huggingface_hub/main/en/guides/inference — use InferenceClient feature_extraction
# No manual URL, no sentence_transformers local.
HF_CODESEARCH_MODEL = "flax-sentence-embeddings/st-codesearch-distilroberta-base"
HF_FALLBACK_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


async def _hf_code_embedding(text: str, *, max_chars: int = 2000) -> list[float] | None:
    """Normal HF embeddings via InferenceClient feature_extraction."""
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token or not text:
        return None
    snippet = text[:max_chars]
    try:
        from huggingface_hub import InferenceClient
        import asyncio
        def _call(model: str) -> list[float] | None:
            try:
                client = InferenceClient(token=token)
                # feature_extraction returns np.ndarray or list
                emb = client.feature_extraction(snippet, model=model)
                # Normalize to list[float]
                try:
                    import numpy as np
                    if isinstance(emb, np.ndarray):
                        # single text -> 1D array
                        return [float(x) for x in emb.tolist()]
                except Exception:
                    pass
                if isinstance(emb, list) and emb and isinstance(emb[0], (int, float)):
                    return [float(x) for x in emb]
                if isinstance(emb, list) and emb and isinstance(emb[0], list):
                    return [float(x) for x in emb[0]]
                return None
            except Exception as exc:
                LOGGER.debug("HF embedding %s failed: %s", model, exc)
                return None
        # Try primary code model, then fallback
        for model in (HF_CODESEARCH_MODEL, HF_FALLBACK_MODEL):
            result = await asyncio.to_thread(_call, model)
            if result is not None:
                return result
        return None
    except Exception as exc:
        LOGGER.debug("HF embedding error: %s", exc)
        return None


async def _hf_batch_code_embeddings(texts: list[str], *, max_chars: int = 2000, batch_size: int = 16) -> list[list[float] | None]:
    """Batch normal HF embeddings — uses same InferenceClient per batch."""
    if not texts:
        return []
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        return [None] * len(texts)
    truncated = [t[:max_chars] for t in texts]
    results: list[list[float] | None] = [None] * len(truncated)
    semaphore = asyncio.Semaphore(3)

    async def _embed_batch(batch: list[str], start_idx: int) -> None:
        async with semaphore:
            try:
                from huggingface_hub import InferenceClient
                import asyncio
                def _call_batch(model: str) -> list[list[float]] | None:
                    try:
                        client = InferenceClient(token=token)
                        embs = client.feature_extraction(batch, model=model)
                        import numpy as np
                        if isinstance(embs, np.ndarray):
                            # batch -> 2D array [batch, dim]
                            if embs.ndim == 2 and embs.shape[0] == len(batch):
                                return [[float(x) for x in row.tolist()] for row in embs]
                            if embs.ndim == 1:
                                return [[float(x) for x in embs.tolist()]]
                        if isinstance(embs, list):
                            # list of lists
                            out: list[list[float]] = []
                            for e in embs:
                                if isinstance(e, list) and e and isinstance(e[0], (int, float)):
                                    out.append([float(x) for x in e])
                                elif isinstance(e, list) and e and isinstance(e[0], list):
                                    out.append([float(x) for x in e[0]])
                            if len(out) == len(batch):
                                return out
                        return None
                    except Exception as exc:
                        LOGGER.debug("HF batch %s failed: %s", model, exc)
                        return None
                for model in (HF_CODESEARCH_MODEL, HF_FALLBACK_MODEL):
                    batch_result = await asyncio.to_thread(_call_batch, model)
                    if batch_result is not None and len(batch_result) == len(batch):
                        for i, vec in enumerate(batch_result):
                            results[start_idx + i] = vec
                        return
                # per-item fallback
                for i, txt in enumerate(batch):
                    emb = await _hf_code_embedding(txt, max_chars=max_chars)
                    results[start_idx + i] = emb
            except Exception as exc:
                LOGGER.debug("HF batch error: %s", exc)

    batches = [(truncated[i : i + batch_size], i) for i in range(0, len(truncated), batch_size)]
    await asyncio.gather(*[_embed_batch(b, s) for b, s in batches])
    return results


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
    except Exception:
        return 0.0
TTL_SECONDS = settings.code_fetch_snapshot_ttl_seconds
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
MAX_EXTRACTED_BYTES = 120 * 1024 * 1024
MAX_FILES = 4_000
MAX_FILE_BYTES = 1_000_000
MAX_LIVE_SNAPSHOTS = 4
MAX_SNIPPET_CHARS = int(os.environ.get("CODE_FETCH_MAX_SNIPPET_CHARS", "0"))
if MAX_SNIPPET_CHARS < 0:
    MAX_SNIPPET_CHARS = 0
MAX_CONTENT_CHARS = int(os.environ.get("CODE_FETCH_MAX_CONTENT_CHARS", "0"))
if MAX_CONTENT_CHARS < 0:
    MAX_CONTENT_CHARS = 0
GRAPH_WAIT_SECONDS = float(os.environ.get("CODE_FETCH_GRAPH_WAIT_SECONDS", "10.0"))
if GRAPH_WAIT_SECONDS < 0:
    GRAPH_WAIT_SECONDS = 0.0


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
    snippet: str | None = None
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
    requested_ref: str = ""
    graph_task: Any = None
    graph_status: str = "pending"
    graph_error: str | None = None
    graph_symbol_count: int = 0
    graph_edge_count: int = 0

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
    has_more: bool = False
    error: str | None = None


class SnapshotError(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _repo_key(snapshot: Snapshot) -> str:
    if not snapshot.requested_ref:
        return snapshot.repository
    return f"{snapshot.repository}@{snapshot.requested_ref}"


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

    def _restore_persisted_snapshot(self, repository: str, key: str) -> Snapshot | None:
        """Restore a recent materialized snapshot whose worktree still exists."""
        with self._lock:
            con = self._connect()
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
            try:
                snapshot.graph_task = asyncio.create_task(
                    self._deferred_graph_build(snapshot)
                )
            except RuntimeError:
                pass  # No loop (some test paths) — status stays pending
        self._remember(snapshot, key=key)
        return snapshot


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
        repo_key = _repo_key(snapshot)
        with self._lock:
            con = self._connect()
            try:
                with con:
                    con.execute("DELETE FROM files WHERE repository = ?", (repo_key,))
                    con.execute("DELETE FROM symbols WHERE repository = ?", (repo_key,))
                    con.execute("DELETE FROM edges WHERE repository = ?", (repo_key,))
                    try:
                        con.execute(
                            "DELETE FROM files_fts WHERE repository = ?",
                            (repo_key,),
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
                    try:
                        con.executemany(
                            "INSERT INTO files_fts (repository, path, content) VALUES (?, ?, ?)",
                            [
                                (repo_key, path, content)
                                for path, _language, _size, content in records
                            ],
                        )
                    except sqlite3.OperationalError:
                        pass
            finally:
                con.close()

    def _remember(self, snapshot: Snapshot, key: str | None = None) -> None:
        self._live[key or snapshot.repository] = snapshot
        if len(self._live) <= MAX_LIVE_SNAPSHOTS:
            return
        oldest_key = min(self._live.keys(), key=lambda k: self._live[k].created_at)
        target_key = key or snapshot.repository
        if oldest_key != target_key:
            self._live.pop(oldest_key, None)

    async def _deferred_graph_build(self, snapshot: Snapshot) -> None:
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
                    records.append((relative.as_posix(), language_for_path(relative.as_posix()), len(data), text))
                except Exception:
                    continue
            symbols, edges = await asyncio.to_thread(_extract_graph, records)
            with self._lock:
                con = self._connect()
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
            LOGGER.info("Deferred graph build completed for %s: %d symbols, %d edges", snapshot.repository, len(symbols), len(edges))
        except Exception as exc:
            snapshot.graph_status = "failed"
            snapshot.graph_error = str(exc)[:500]
            LOGGER.warning("Deferred graph build failed for %s: %s", snapshot.repository, exc)

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
        persisted = self._restore_persisted_snapshot(repository, key)
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
                self._remember(previous, key=key)
                return previous
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
            shutil.rmtree(root, ignore_errors=True)
            self._remember(snapshot, key=key)
            # TreeSitter graph (symbols/edges) is expensive (30-50% of cold time)
            # and only needed for symbol/callers queries. Defer it so the
            # snapshot is usable for search/read/tree immediately.
            try:
                snapshot.graph_task = asyncio.create_task(self._deferred_graph_build(snapshot))
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
                    content_out = sliced_text if MAX_CONTENT_CHARS <= 0 else sliced_text[:MAX_CONTENT_CHARS]
                    is_truncated = False if MAX_CONTENT_CHARS <= 0 else len(sliced_text) > MAX_CONTENT_CHARS
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
                raw_content_out = raw_text if MAX_CONTENT_CHARS <= 0 else raw_text[:MAX_CONTENT_CHARS]
                raw_truncated = False if MAX_CONTENT_CHARS <= 0 else len(raw_text) > MAX_CONTENT_CHARS
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

        hits, truncated, has_more = self._search_hits(
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
            graph_hits = self._graph_hits(
                snapshot, normalized_symbol, limit=limit, context_lines=context
            )
            hits = _merge_hits(hits, graph_hits, limit)
        return QueryResult(
            snapshot=snapshot,
            intent="search",
            hits=hits,
            truncated=truncated or len(hits) >= limit,
            has_more=has_more,
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
        offset: int = 0,
        language: str | None = None,
        filename: str | None = None,
        path_glob: str | None = None,
        exclude_glob: str | None = None,
        case_sensitive: bool = False,
    ) -> tuple[list[SnapshotHit], bool, bool]:
        offset = max(0, offset)
        # Over-fetch by one so a full page can prove has_more without a re-query.
        fetch_limit = offset + limit + 1
        filters = {
            "language": language,
            "filename": filename,
            "path_glob": path_glob,
            "exclude_glob": exclude_glob,
        }
        if regexp:
            try:
                pattern = re.compile(query)
            except re.error as exc:
                raise SnapshotError(f"invalid regular expression: {exc}") from exc
            scanned, scan_truncated = _scan_regex(
                snapshot.root,
                pattern,
                path_prefix=path_prefix,
                limit=fetch_limit,
                context_lines=context_lines,
                case_sensitive=case_sensitive,
                **filters,
            )
            page = scanned[offset : offset + limit]
            has_more = (len(scanned) > offset + limit or scan_truncated) and bool(page)
            return page, scan_truncated, has_more
        hits: list[SnapshotHit] = []
        fts_hits = self._fts_hits(
            _repo_key(snapshot),
            query,
            path_prefix=path_prefix,
            limit=fetch_limit,
            case_sensitive=case_sensitive,
            **filters,
        )
        if fts_hits:
            hits = [
                self._hit_from_file(snapshot, path, query, context_lines=context_lines, why=["fts"])
                for path in fts_hits
            ]
        # FTS uses an AND expression first. If no file contains every term,
        # search each term so natural-language queries still return useful
        # repository-wide candidates instead of a false empty result.
        if not fts_hits and " " in query.strip():
            terms = [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query) if len(t) > 1]
            if len(terms) > 1:
                scan_truncated = False
                for term in terms:
                    scanned, term_truncated = _scan_literal(
                        snapshot.root,
                        term,
                        path_prefix=path_prefix,
                        limit=fetch_limit,
                        context_lines=context_lines,
                        case_sensitive=case_sensitive,
                        **filters,
                    )
                    hits = _merge_hits(hits, scanned, fetch_limit)
                    scan_truncated = scan_truncated or term_truncated
                    if len(hits) >= fetch_limit:
                        break
                for hit in hits:
                    hit.callers, hit.callees = self._neighbors(
                        _repo_key(snapshot), hit.symbol_name or query, hit.path
                    )
                    if hit.symbol_name is None:
                        symbol = self._symbol_at(_repo_key(snapshot), hit.path, hit.start_line)
                        if symbol is not None:
                            hit.symbol_name, hit.symbol_kind, hit.role = symbol
                page = hits[offset : offset + limit]
                has_more = (len(hits) > offset + limit or scan_truncated) and bool(page)
                return page, scan_truncated or len(hits) >= fetch_limit, has_more
        scanned, scan_truncated = _scan_literal(
            snapshot.root,
            query,
            path_prefix=path_prefix,
            limit=fetch_limit,
            context_lines=context_lines,
            case_sensitive=case_sensitive,
            **filters,
        )

        hits = _merge_hits(hits, scanned, fetch_limit)
        for hit in hits:
            hit.callers, hit.callees = self._neighbors(
                _repo_key(snapshot), hit.symbol_name or query, hit.path
            )
            if hit.symbol_name is None:
                symbol = self._symbol_at(_repo_key(snapshot), hit.path, hit.start_line)
                if symbol is not None:
                    hit.symbol_name, hit.symbol_kind, hit.role = symbol
        page = hits[offset : offset + limit]
        has_more = (len(hits) > offset + limit or scan_truncated) and bool(page)
        return page, scan_truncated or len(hits) >= fetch_limit, has_more

    async def _semantic_search_hits(
        self,
        snapshot: Snapshot,
        query: str,
        *,
        path_prefix: str | None,
        limit: int,
        context_lines: int,
    ) -> list[SnapshotHit]:
        """Semantic fallback via HF st-codesearch-distilroberta-base.

        Only invoked when FTS+literal yield 0 hits and HF_TOKEN present.
        Batches file snippets (path + first 1k chars) to HF Inference,
        compares via cosine to query embedding, returns top hits with
        why=["semantic"] and confidence = cosine similarity.
        """
        # Gate: token required
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if not token:
            return []
        if not query or len(query.strip()) < 3:
            return []
        query_emb = await _hf_code_embedding(query, max_chars=2000)
        if query_emb is None:
            return []
        # Gather candidate files — cap to avoid 2000 HF calls
        try:
            candidates = list(_iter_files(snapshot.root, path_prefix))
        except Exception:
            return []
        if not candidates:
            return []
        # Repo-wide semantic is expensive — cap at 300 files (balanced vs cost)
        # Path-scoped searches keep all candidates (cheap, already filtered)
        if path_prefix is None and len(candidates) > 300:
            # Prioritize smaller files / recently modified could help, but simple slice is deterministic
            candidates = candidates[:300]
        candidate_texts: list[str] = []
        candidate_paths: list[Path] = []
        for p in candidates:
            try:
                txt = _read_text(p)
                rel = p.relative_to(snapshot.root).as_posix()
                snippet_for_emb = f"{rel}\n{txt[:1200]}"
                candidate_texts.append(snippet_for_emb[:2000])
                candidate_paths.append(p)
            except Exception:
                continue
        if not candidate_texts:
            return []
        embeddings = await _hf_batch_code_embeddings(candidate_texts, max_chars=2000, batch_size=16)
        scored: list[tuple[float, Path]] = []
        for path, emb in zip(candidate_paths, embeddings):
            if emb is None:
                continue
            sim = _cosine_similarity(query_emb, emb)
            # Code RAG best practice: Top-K 3-5, calibrated threshold, not universal.
            # Web search shows typical RAG thresholds 0.6-0.75 for high precision (vs 0.15 permissive
            # would return noise). Use 0.60 for code (L2-normalized embeddings) with adaptive fallback:
            # if we have <3 candidates above 0.60, relax to 0.50, but never below 0.45 for code.
            # This avoids nonsense queries returning noise while keeping real code queries.
            if sim > 0.60:
                scored.append((sim, path))
        # Adaptive relaxation: if strict 0.60 yields <3 hits but we have high-ish scores, relax to 0.50
        if len(scored) < 3:
            for path, emb in zip(candidate_paths, embeddings):
                if emb is None:
                    continue
                sim = _cosine_similarity(query_emb, emb)
                if 0.50 < sim <= 0.60:
                    # avoid duplicates already in scored
                    if not any(p == path for _, p in scored):
                        scored.append((sim, path))
        if not scored:
            return []
        scored.sort(key=lambda x: x[0], reverse=True)
        hits: list[SnapshotHit] = []
        for sim, path in scored[:limit]:
            rel = path.relative_to(snapshot.root).as_posix()
            # Build snippet around best understanding: use _first_match_snippet fallback
            try:
                start, end, snippet = _first_match_snippet(path, query, context_lines)
            except Exception:
                snippet = ""
                start, end = 1, 1
            hits.append(
                SnapshotHit(
                    path=rel,
                    start_line=start,
                    end_line=end,
                    why=["semantic"],
                    snippet=snippet,
                    confidence=float(sim),
                )
            )
        # Enrich with neighbors/symbol like _search_hits does
        for hit in hits:
            try:
                hit.callers, hit.callees = self._neighbors(_repo_key(snapshot), hit.symbol_name or query, hit.path)
                if hit.symbol_name is None:
                    sym = self._symbol_at(_repo_key(snapshot), hit.path, hit.start_line)
                    if sym is not None:
                        hit.symbol_name, hit.symbol_kind, hit.role = sym
            except Exception:
                continue
        return hits

    async def _search_hits_async(
        self,
        snapshot: Snapshot,
        query: str,
        *,
        path_prefix: str | None,
        regexp: bool,
        limit: int,
        context_lines: int,
    ) -> tuple[list[SnapshotHit], bool]:
        """Async search with semantic fallback when FTS+literal miss and HF_TOKEN present.

        Preserves sync _search_hits semantics for all existing callers; semantic
        hits are merged via RRF-like _merge_hits (dedup by path+line). If FTS
        yielded hits, semantic is not invoked (to avoid cost) — fallback only
        when full sync search returns 0 hits. Caller can extend to RRF fusion
        of FTS and semantic via _merge_hits ordering (currently semantic after).
        """
        if regexp:
            # Semantic fallback never for regex
            hits, _truncated, _has_more = self._search_hits(snapshot, query, path_prefix=path_prefix, regexp=True, limit=limit, context_lines=context_lines)
            return hits, _truncated
        hits, truncated, _has_more = self._search_hits(snapshot, query, path_prefix=path_prefix, regexp=False, limit=limit, context_lines=context_lines)
        if hits or truncated:
            return hits, truncated
        # No hits — try semantic if token present
        sem_hits = await self._semantic_search_hits(snapshot, query, path_prefix=path_prefix, limit=limit, context_lines=context_lines)
        if sem_hits:
            # RRF-like merge: keep semantic hits as result; if we later have FTS hits,
            # merge would be _merge_hits(hits, sem_hits, limit)
            return sem_hits[:limit], False
        return hits, truncated

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
        """Async counterpart to query() with HF semantic fallback.

        For read/tree/graph/map intents, delegates to sync query(). For search
        intent with 0 hits, attempts semantic fallback via st-codesearch-distilroberta-base
        when HF_TOKEN is set, merging via deduplication (RRF-like). Falls back
        gracefully if HF unavailable.
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
            sem_hits, sem_trunc = await self._search_hits_async(
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
                    graph_hits = self._graph_hits(snapshot, (symbol or "").strip(), limit=limit, context_lines=context)
                    hits = _merge_hits(hits, graph_hits, limit)
                return QueryResult(
                    snapshot=snapshot,
                    intent="search",
                    hits=hits,
                    truncated=sem_trunc or len(hits) >= limit,
                )
        except Exception as exc:
            LOGGER.debug("semantic fallback failed: %s", exc)
        return base

    def _fts_hits(
        self,
        repo_key: str,
        query: str,
        *,
        path_prefix: str | None,
        limit: int,
        language: str | None = None,
        filename: str | None = None,
        path_glob: str | None = None,
        exclude_glob: str | None = None,
        case_sensitive: bool = False,
    ) -> list[str]:
        # FTS5 MATCH is case-insensitive; when case-sensitive matching is
        # requested the literal scan carries the query instead.
        if case_sensitive:
            return []
        terms = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query) if len(token) > 1]
        if not terms:
            return []
        terms = terms[:8]

        def _run(match: str) -> list[sqlite3.Row]:
            base_sql = "SELECT path FROM files_fts WHERE repository = ? AND files_fts MATCH ?"
            args: list[Any] = [repo_key, match]
            if path_prefix:
                base_sql += " AND path LIKE ?"
                args.append(f"{path_prefix}%")
            ranked_sql = f"{base_sql} ORDER BY bm25(files_fts) LIMIT ?"
            ranked_args = [*args, limit]
            with self._lock:
                con = self._connect()
                try:
                    try:
                        return con.execute(ranked_sql, ranked_args).fetchall()
                    except sqlite3.OperationalError:
                        return con.execute(f"{base_sql} LIMIT ?", [*args, limit]).fetchall()
                except sqlite3.OperationalError:
                    return []
                finally:
                    con.close()

        rows = _run(" AND ".join(f'"{term}"' for term in terms))
        if not rows and len(terms) > 1:
            rows = _run(" OR ".join(f'"{term}"' for term in terms))
        return [
            path
            for path in (str(row["path"]) for row in rows)
            if _matches_filters(
                path,
                language=language,
                filename=filename,
                path_glob=path_glob,
                exclude_glob=exclude_glob,
            )
        ]

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
                    (_repo_key(snapshot), symbol, limit),
                ).fetchall()
            finally:
                con.close()
        hits: list[SnapshotHit] = []
        for row in defs:
            path = str(row["path"])
            start_line = int(row["start_line"])
            end_line = int(row["end_line"])
            callers, callees = self._neighbors(_repo_key(snapshot), symbol, path)
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
            for path in self._fts_hits(_repo_key(snapshot), symbol, path_prefix=None, limit=limit)
        ]

    def _neighbors(
        self,
        repo_key: str,
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
                    "WHERE repository = ? AND target_name = ? AND target_path IS NOT NULL LIMIT 3",
                    (repo_key, name),
                ).fetchall()
                callees = con.execute(
                    "SELECT target_name, target_path, target_line FROM edges "
                    "WHERE repository = ? AND source_name = ? AND source_path = ? "
                    "AND target_path IS NOT NULL LIMIT 3",
                    (repo_key, name, path),
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
        self, repo_key: str, path: str, line: int
    ) -> tuple[str, str | None, str] | None:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT name, kind FROM symbols WHERE repository = ? AND path = ? "
                    "AND start_line <= ? AND end_line >= ? LIMIT 1",
                    (repo_key, path, line, line),
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
                    (_repo_key(snapshot),),
                ).fetchall()
                top_symbols = con.execute(
                    "SELECT name, kind, path, start_line FROM symbols WHERE repository = ? "
                    "ORDER BY start_line LIMIT 20",
                    (_repo_key(snapshot),),
                ).fetchall()
            finally:
                con.close()
        files_list = _list_tree(snapshot.root, "", limit=200, depth=None)
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
            "files": files_list,
            "files_truncated": len(files_list) >= 200,
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


async def _resolve_main_commit(repository: str, *, ref: str | None = None) -> tuple[str, str]:
    token = _token()
    client = await get_http_client()
    owner, repo = repository.split("/", 1)
    repo_url = f"{_GITHUB_API_URL}/repos/{quote(owner)}/{quote(repo)}"
    if ref:
        try:
            commit_response = await client.get(
                f"{repo_url}/commits/{quote(ref)}",
                headers=_headers(token),
                timeout=settings.search_retrieve_budget_seconds,
            )
        except httpx.HTTPError as exc:
            raise SnapshotError(
                f"GitHub commit lookup for ref '{ref}' failed: {str(exc) or type(exc).__name__}"
            ) from exc
        if commit_response.status_code == 200:
            commit_payload = commit_response.json() if commit_response.content else {}
            sha = commit_payload.get("sha") if isinstance(commit_payload, dict) else None
            if isinstance(sha, str) and len(sha) >= 7:
                return ref, sha
        raise SnapshotError(
            f"GitHub commit lookup for ref '{ref}' returned HTTP {commit_response.status_code}",
            retry_after_seconds=_retry_after(commit_response),
        )
    try:
        response = await client.get(
            repo_url, headers=_headers(token), timeout=settings.search_retrieve_budget_seconds
        )
    except httpx.HTTPError as exc:
        raise SnapshotError(
            f"GitHub repository lookup failed: {str(exc) or type(exc).__name__}"
        ) from exc
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
        raise SnapshotError(
            f"GitHub commit lookup failed: {str(exc) or type(exc).__name__}"
        ) from exc
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
        raise SnapshotError(
            f"GitHub tarball download failed: {str(exc) or type(exc).__name__}"
        ) from exc
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
) -> tuple[int, bool, int, list[tuple[str, str | None, int, str]]]:
    records: list[tuple[str, str | None, int, str]] = []
    truncated = False
    copied = 0
    skipped_binary = 0
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
            skipped_binary += 1
            continue
        text = data.decode("utf-8", errors="replace")
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append((relative.as_posix(), language_for_path(relative.as_posix()), len(data), text))
        copied += 1
    return copied, truncated, skipped_binary, records


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


def _list_tree(root: Path, prefix: str, *, limit: int, depth: int | None = None) -> list[str]:
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
        if depth is not None:
            rel_from_base = path.relative_to(base)
            if len(rel_from_base.parts) > depth:
                continue
        entries.append(relative.as_posix())
        if len(entries) >= limit:
            break
    return entries


def _matches_filters(
    rel_path: str,
    *,
    language: str | None,
    filename: str | None,
    path_glob: str | None,
    exclude_glob: str | None,
) -> bool:
    """Empty filters pass everything; each set filter must hold."""
    if language:
        inferred = language_for_path(rel_path) or ""
        if inferred.casefold() != language.strip().casefold():
            return False
    if filename and not fnmatch(Path(rel_path).name, filename):
        return False
    if path_glob and not fnmatch(rel_path, path_glob):
        return False
    if exclude_glob and fnmatch(rel_path, exclude_glob):
        return False
    return True


def _try_ripgrep_scan(
    root: Path,
    query: str,
    *,
    path_prefix: str | None,
    limit: int,
    context_lines: int,
    is_regex: bool,
    case_sensitive: bool = False,
    language: str | None = None,
    filename: str | None = None,
    path_glob: str | None = None,
    exclude_glob: str | None = None,
) -> tuple[list[SnapshotHit], bool] | None:
    """Try ripgrep (rg) for literal/regex scan - 10-50x faster than Python loop."""
    rg_path = shutil.which("rg")
    if rg_path is None:
        # Windows fallback
        win_path = Path(r"C:\Users\Jan\AppData\Local\Programs\Python\Python312\Scripts\rg.EXE")
        if win_path.exists():
            rg_path = str(win_path)
        else:
            return None
    try:
        search_root = root / path_prefix if path_prefix else root
        if not search_root.exists():
            return [], False
        # Use rg --json for structured output, handle literal vs regex
        cmd = [
            rg_path,
            "--json",
            "--no-config",
            "--hidden",
            "--glob", "!.git/*",
            "--max-count", str(limit),
            "--context", str(context_lines),
        ]
        if not is_regex:
            cmd.append("--fixed-strings")
        if not case_sensitive:
            cmd.append("--ignore-case")
        cmd.extend(["--", query, str(search_root)])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=8, encoding="utf-8", errors="replace")
        if result.returncode not in (0, 1):
            return None
        hits: list[SnapshotHit] = []
        # Parse rg --json: each line is JSON with type "match" or "context"
        # We only care about "match" lines; context is handled via snippet window
        for line in result.stdout.splitlines():
            try:
                obj = __import__("json").loads(line)
            except Exception:
                continue
            if obj.get("type") != "match":
                continue
            data = obj.get("data", {})
            path_info = data.get("path", {})
            abs_path = path_info.get("text", "")
            line_num = data.get("line_number", 1)
            try:
                file_path = Path(abs_path)
                relative = file_path.relative_to(root).as_posix()
            except Exception:
                continue
            if not _matches_filters(
                relative,
                language=language,
                filename=filename,
                path_glob=path_glob,
                exclude_glob=exclude_glob,
            ):
                continue
            # For simplicity, re-read file and build snippet window as before
            try:
                lines = _read_text(file_path).splitlines()
                start = max(1, line_num - context_lines)
                end = min(len(lines), line_num + context_lines)
                snippet_text = "\n".join(lines[start - 1 : end])
                if MAX_SNIPPET_CHARS > 0:
                    snippet_text = snippet_text[:MAX_SNIPPET_CHARS]
                hits.append(
                    SnapshotHit(
                        path=relative,
                        start_line=line_num,
                        end_line=line_num,
                        why=["literal" if not is_regex else "regex"],
                        snippet=snippet_text,
                        confidence=0.85,
                    )
                )
                if len(hits) >= limit:
                    return hits, True
            except Exception:
                continue
        return hits, False
    except Exception:
        return None


def _scan_literal(
    root: Path,
    query: str,
    *,
    path_prefix: str | None,
    limit: int,
    context_lines: int,
    case_sensitive: bool = False,
    language: str | None = None,
    filename: str | None = None,
    path_glob: str | None = None,
    exclude_glob: str | None = None,
) -> tuple[list[SnapshotHit], bool]:
    # Try ripgrep first - 10-50x faster
    rg_result = _try_ripgrep_scan(
        root,
        query,
        path_prefix=path_prefix,
        limit=limit,
        context_lines=context_lines,
        is_regex=False,
        case_sensitive=case_sensitive,
        language=language,
        filename=filename,
        path_glob=path_glob,
        exclude_glob=exclude_glob,
    )
    if rg_result is not None:
        return rg_result
    needle = query if case_sensitive else query.casefold()
    hits: list[SnapshotHit] = []
    scanned_files = 0
    for path in _iter_files(root, path_prefix):
        scanned_files += 1
        if path_prefix is None and scanned_files > 2000:
            return hits, True
        relative = path.relative_to(root).as_posix()
        if not _matches_filters(
            relative,
            language=language,
            filename=filename,
            path_glob=path_glob,
            exclude_glob=exclude_glob,
        ):
            continue
        lines = _read_text(path).splitlines()
        for index, line in enumerate(lines, start=1):
            if case_sensitive:
                if needle not in line:
                    continue
            elif needle not in line.casefold():
                continue
            start = max(1, index - context_lines)
            end = min(len(lines), index + context_lines)
            snippet_text = "\n".join(lines[start - 1 : end])
            if MAX_SNIPPET_CHARS > 0:
                snippet_text = snippet_text[:MAX_SNIPPET_CHARS]
            hits.append(
                SnapshotHit(
                    path=relative,
                    start_line=index,
                    end_line=index,
                    why=["literal"],
                    snippet=snippet_text,
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
    case_sensitive: bool = False,
    language: str | None = None,
    filename: str | None = None,
    path_glob: str | None = None,
    exclude_glob: str | None = None,
) -> tuple[list[SnapshotHit], bool]:
    # Try ripgrep first
    rg_result = _try_ripgrep_scan(
        root,
        pattern.pattern,
        path_prefix=path_prefix,
        limit=limit,
        context_lines=context_lines,
        is_regex=True,
        case_sensitive=case_sensitive,
        language=language,
        filename=filename,
        path_glob=path_glob,
        exclude_glob=exclude_glob,
    )
    if rg_result is not None:
        return rg_result
    hits: list[SnapshotHit] = []
    scanned_files = 0
    for path in _iter_files(root, path_prefix):
        scanned_files += 1
        if path_prefix is None and scanned_files > 2000:
            return hits, True
        relative = path.relative_to(root).as_posix()
        if not _matches_filters(
            relative,
            language=language,
            filename=filename,
            path_glob=path_glob,
            exclude_glob=exclude_glob,
        ):
            continue
        lines = _read_text(path).splitlines()
        for index, line in enumerate(lines, start=1):
            if not pattern.search(line):
                continue
            start = max(1, index - context_lines)
            end = min(len(lines), index + context_lines)
            snippet_text = "\n".join(lines[start - 1 : end])
            if MAX_SNIPPET_CHARS > 0:
                snippet_text = snippet_text[:MAX_SNIPPET_CHARS]
            hits.append(
                SnapshotHit(
                    path=relative,
                    start_line=index,
                    end_line=index,
                    why=["regex"],
                    snippet=snippet_text,
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
            snippet = "\n".join(lines[start - 1 : end])
            if MAX_SNIPPET_CHARS > 0:
                snippet = snippet[:MAX_SNIPPET_CHARS]
            return index, index, snippet
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
                snippet = "\n".join(lines[start - 1 : end])
                if MAX_SNIPPET_CHARS > 0:
                    snippet = snippet[:MAX_SNIPPET_CHARS]
                return index, index, snippet
    snippet = "\n".join(lines[: max(1, context_lines * 2 + 1)])
    if MAX_SNIPPET_CHARS > 0:
        snippet = snippet[:MAX_SNIPPET_CHARS]
    return 1, min(len(lines) or 1, context_lines * 2 + 1), snippet


def _snippet(path: Path, start_line: int, end_line: int, context_lines: int) -> str:
    if not path.is_file():
        return ""
    lines = _read_text(path).splitlines()
    start = max(1, start_line - context_lines)
    end = min(len(lines), end_line + context_lines)
    snippet = "\n".join(lines[start - 1 : end])
    if MAX_SNIPPET_CHARS > 0:
        snippet = snippet[:MAX_SNIPPET_CHARS]
    return snippet


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
    "_hf_code_embedding",
    "_cosine_similarity",
    "get_snapshot_manager",
    "reset_snapshot_manager_for_tests",
]
