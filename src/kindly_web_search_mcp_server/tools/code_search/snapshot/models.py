"""Dataclasses, exceptions and shared constants for the snapshot package.

Holds the types other modules exchange (`Snapshot`, `SnapshotHit`, `QueryResult`,
`RelatedSymbol`, `SnapshotError`) plus the budgets and skip lists that drive
every acquisition / scan / persist path.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ....settings import settings

LOGGER = logging.getLogger(__name__)

TTL_SECONDS = settings.code_fetch_snapshot_ttl_seconds
MAX_ARCHIVE_BYTES = int(os.environ.get("CODE_FETCH_MAX_ARCHIVE_BYTES", str(256 * 1024 * 1024)))
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
    ".pptx",
    ".docx",
    ".xlsx",
    ".mp4",
    ".mov",
    ".avi",
    ".mp3",
    ".wav",
    ".npy",
    ".npz",
    ".pth",
    ".pt",
    ".onnx",
    ".safetensors",
    ".h5",
    ".parquet",
    ".tar",
}


_SPARSE_SKIP_PATTERNS = [
    "/*",
    "!*.gif",
    "!*.png",
    "!*.jpg",
    "!*.jpeg",
    "!*.webp",
    "!*.ico",
    "!*.pdf",
    "!*.pptx",
    "!*.docx",
    "!*.xlsx",
    "!*.zip",
    "!*.tar*",
    "!*.gz",
    "!*.tgz",
    "!*.bin",
    "!*.exe",
    "!*.whl",
    "!*.so",
    "!*.dll",
    "!*.dylib",
    "!*.mp4",
    "!*.mov",
    "!*.avi",
    "!*.mp3",
    "!*.wav",
    "!*.npy",
    "!*.npz",
    "!*.pth",
    "!*.pt",
    "!*.onnx",
    "!*.safetensors",
    "!*.h5",
    "!*.wasm",
    "!*.parquet",
    "!docs/images/**",
    "!tests/testdata/**",
    "!*testdata*/**",
]


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
    """Repository key for SQLite rows: ``repo`` or ``repo@ref`` when a ref is set."""
    if not snapshot.requested_ref:
        return snapshot.repository
    return f"{snapshot.repository}@{snapshot.requested_ref}"
