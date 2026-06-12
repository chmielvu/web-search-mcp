"""Fixed repository root paths for DuckDB data storage.

All analytics, cache, and experiment data stored under repo_root/duckdb_data/
to avoid creating .kindly folders everywhere.
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

# Subdirectories
ANALYTICS_DIR = DUCKDB_DATA_DIR / "analytics"
CACHE_DIR = DUCKDB_DATA_DIR / "cache"
EXTENSIONS_DIR = DUCKDB_DATA_DIR / "duckdb_extensions"
LOGS_DIR = DUCKDB_DATA_DIR / "logs"
TRAINING_DIR = DUCKDB_DATA_DIR / "training"
EXPERIMENTS_DIR = DUCKDB_DATA_DIR / "experiments"


def ensure_duckdb_dirs() -> None:
    """Create all DuckDB data directories if they don't exist."""
    for dir_path in [
        DUCKDB_DATA_DIR,
        ANALYTICS_DIR,
        CACHE_DIR,
        EXTENSIONS_DIR,
        LOGS_DIR,
        TRAINING_DIR,
        EXPERIMENTS_DIR,
    ]:
        dir_path.mkdir(parents=True, exist_ok=True)


# Default file paths (fixed locations)
DEFAULT_ANALYTICS_DB = str(ANALYTICS_DIR / "search_events.duckdb")
DEFAULT_PAGE_CACHE_DB = str(CACHE_DIR / "page_cache.duckdb")
DEFAULT_PROCESS_LOGS_DB = str(LOGS_DIR / "process_logs.duckdb")
DEFAULT_QUERY_UNDERSTANDING_JSONL = str(TRAINING_DIR / "query_understanding.jsonl")
DEFAULT_EXPERIMENTS_YAML = str(EXPERIMENTS_DIR / "experiments.yaml")
DEFAULT_EXTENSION_DIR = str(EXTENSIONS_DIR)