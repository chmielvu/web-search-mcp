"""Shared connection helpers for DuckDB writers."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from ...settings import settings

if TYPE_CHECKING:
    pass

_LOCK = threading.Lock()


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


__all__ = ["_LOCK", "_db_path", "_ensure_columns", "duckdb"]
