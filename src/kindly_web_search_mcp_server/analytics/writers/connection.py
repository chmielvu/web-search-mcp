"""Shared connection helpers for DuckDB writers."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import duckdb

from ...settings import settings

_LOCK = threading.Lock()
_flockmtl_loaded = False
logger = logging.getLogger(__name__)


def _db_path(db_path: str | None = None) -> Path:
    """Resolve the DuckDB file path, falling back to the configured default."""
    return Path(db_path or settings.analytics_duckdb_path)


def _ensure_columns(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    additions: dict[str, str],
) -> None:
    """Add missing columns to an existing table (idempotent ALTERs)."""
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
    for column, column_type in additions.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {column_type}")


def ensure_flockmtl(connection: duckdb.DuckDBPyConnection) -> bool:
    """Load FlockMTL extension if enabled in settings.

    Returns True if FlockMTL was loaded successfully or was already loaded.
    Idempotent — safe to call on every connection open.
    """
    global _flockmtl_loaded
    if not settings.flockmtl_enabled:
        return False
    if _flockmtl_loaded:
        return True
    try:
        connection.execute("INSTALL flockmtl FROM community")
        connection.execute("LOAD flockmtl")
        _flockmtl_loaded = True
        logger.info("FlockMTL extension loaded")
        return True
    except Exception:
        logger.warning("FlockMTL not available — semantic SQL analytics disabled", exc_info=True)
        return False


__all__ = ["_LOCK", "_db_path", "_ensure_columns", "ensure_flockmtl", "duckdb"]
