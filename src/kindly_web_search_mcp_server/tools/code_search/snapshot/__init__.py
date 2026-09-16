"""Snapshot package public surface.

Re-exports every name that was previously importable from
``kindly_web_search_mcp_server.tools.code_search.snapshot``. Module layout:

- :mod:`.models` — dataclasses, exceptions, shared constants.
- :mod:`.fetch` — repository acquisition (clone / tarball / extract / collect).
- :mod:`.scan` — searching helpers (ripgrep + Python literal/regex fallbacks).
- :mod:`.graph` — symbol/edge graph extraction.
- :mod:`.embeddings` — HuggingFace embedding helpers.
- :mod:`.store` — :class:`SnapshotManager` lifecycle + module-level factory.
"""

from __future__ import annotations

from .embeddings import _cosine_similarity, _hf_code_embedding
from .fetch import _resolve_main_commit
from .models import (
    GRAPH_WAIT_SECONDS,
    MAX_CONTENT_CHARS,
    TTL_SECONDS,
    QueryResult,
    RelatedSymbol,
    Snapshot,
    SnapshotError,
    SnapshotHit,
)
from .store import (
    SnapshotManager,
    get_snapshot_manager,
    reset_snapshot_manager_for_tests,
)

__all__ = [
    "TTL_SECONDS",
    "QueryResult",
    "RelatedSymbol",
    "Snapshot",
    "SnapshotError",
    "SnapshotHit",
    "SnapshotManager",
    "_cosine_similarity",
    "_hf_code_embedding",
    "get_snapshot_manager",
    "reset_snapshot_manager_for_tests",
]
