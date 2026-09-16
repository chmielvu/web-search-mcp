"""Fixed repository root paths for storage.

All analytics, cache, and experiment data live under ``repo_root/duckdb_data/``
to avoid creating ``.kindly`` folders everywhere. Index-mode content artifacts
persist under ``repo_root/outputs/`` (see :mod:`content.constructor`).
"""

from __future__ import annotations

from pathlib import Path


def _find_repo_root() -> Path:
    """Find the repository root by looking for .git or pyproject.toml."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    # Fallback: assume standard layout
    return current.parents[3]


REPO_ROOT = _find_repo_root()
DUCKDB_DATA_DIR = REPO_ROOT / "duckdb_data"
OUTPUTS_DIR = REPO_ROOT / "outputs"

# Subdirectories
ANALYTICS_DIR = DUCKDB_DATA_DIR / "analytics"
BLOCKLIST_DIR = DUCKDB_DATA_DIR / "blocklist"
CACHE_DIR = DUCKDB_DATA_DIR / "cache"
EXTENSIONS_DIR = DUCKDB_DATA_DIR / "duckdb_extensions"
LOGS_DIR = DUCKDB_DATA_DIR / "logs"
TELEGRAM_DIR = DUCKDB_DATA_DIR / "telegram"
TRAINING_DIR = DUCKDB_DATA_DIR / "training"


def ensure_duckdb_dirs() -> None:
    """Create all DuckDB data directories if they don't exist."""
    for dir_path in [
        DUCKDB_DATA_DIR,
        ANALYTICS_DIR,
        BLOCKLIST_DIR,
        CACHE_DIR,
        EXTENSIONS_DIR,
        LOGS_DIR,
        TRAINING_DIR,
        TELEGRAM_DIR,
    ]:
        dir_path.mkdir(parents=True, exist_ok=True)


# Default file paths (fixed locations)
DEFAULT_ANALYTICS_DB = str(ANALYTICS_DIR / "search_events.duckdb")
DEFAULT_BLOCKLIST_DB = str(BLOCKLIST_DIR / "blocklist.sqlite")
DEFAULT_PAGE_CACHE_DB = str(CACHE_DIR / "page_cache.sqlite")
DEFAULT_TRANSCRIPT_CACHE_DB = str(CACHE_DIR / "transcript_cache.sqlite")
DEFAULT_CODE_FETCH_SNAPSHOT_DB = str(CACHE_DIR / "code_fetch_snapshots.sqlite")
DEFAULT_PROCESS_LOGS_DB = str(LOGS_DIR / "process_logs.sqlite")
DEFAULT_QUERY_UNDERSTANDING_JSONL = str(TRAINING_DIR / "query_understanding.jsonl")
DEFAULT_EXTENSION_DIR = str(EXTENSIONS_DIR)
