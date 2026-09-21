"""DuckDB FTS (full-text search) bootstrap and BM25 read helpers.

Provides index creation on ``provider_results`` (title, snippet) and
``query_variants`` (query_text), plus parameterised BM25 search queries
against those tables.  FTS indexes are **not** auto-updated when source
rows change — call ``refresh_fts_indexes`` after batch writes.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from ..settings import settings

logger = logging.getLogger(__name__)

_FTS_INSTALLED: bool = False
_VSS_INSTALLED: bool = False

# DuckDB 1.5.x cannot replay a WAL that contains full-text index DDL
# (rebuild drops/recreates the ``fts_main_<table>`` schema) when the
# writing process died before a checkpoint.  Replay fails with a
# dependency error and *every* open of the database fails until the WAL
# is removed.  See the ``vss`` extension persistence note in the DuckDB
# docs: "WAL recovery is not yet properly implemented for custom
# indexes" (https://duckdb.org/docs/lts/core_extensions/vss#persistence).
_WAL_REPLAY_ERROR_MARKER = "Failure while replaying WAL"
_FTS_SCHEMA_MARKER = "fts_main"


def ensure_fts_loaded(connection: duckdb.DuckDBPyConnection) -> bool:
    """Install (once per process) and load the DuckDB FTS extension.

    Returns True when FTS is usable on *connection*.
    """
    global _FTS_INSTALLED
    try:
        if not _FTS_INSTALLED:
            connection.execute("INSTALL fts;")
            _FTS_INSTALLED = True
        connection.execute("LOAD fts;")
        return True
    except Exception:
        logger.debug("FTS extension unavailable", exc_info=True)
        return False


def _table_exists(connection: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT count() FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = ?",
        [table_name],
    ).fetchone()
    return bool(row and row[0] > 0)


def _ensure_vss_loaded_for_checkpoint(connection: duckdb.DuckDBPyConnection) -> bool:
    """Load vss so a CHECKPOINT can serialize persisted HNSW indexes.

    Mirrors ``writers.schema.ensure_vss_loaded`` (not importable from
    here — ``writers.schema`` imports this module).  A checkpoint of a
    database holding HNSW indexes fails with "unknown index type
    'HNSW'" unless the vss extension is loaded on the connection.
    """
    global _VSS_INSTALLED
    try:
        if not _VSS_INSTALLED:
            with contextlib.suppress(Exception):
                connection.execute("INSTALL vss;")
            _VSS_INSTALLED = True
        connection.execute("LOAD vss;")
        connection.execute("SET hnsw_enable_experimental_persistence = true;")
        return True
    except Exception:
        return False


def refresh_fts_indexes(connection: duckdb.DuckDBPyConnection) -> None:
    """Rebuild FTS indexes on ``provider_results`` and ``query_variants``.

    Must be called after batch writes to those tables because DuckDB FTS
    indexes do not auto-update on INSERT.  Silently no-ops when tables
    do not exist or FTS is not loaded.

    A ``CHECKPOINT`` runs after a successful rebuild: replaying a WAL
    that contains FTS index DDL is unreliable, so the DDL must never be
    left in an uncheckpointed WAL.
    """
    try:
        rebuilt = False
        if _table_exists(connection, "provider_results"):
            connection.execute(
                "PRAGMA create_fts_index("
                "'provider_results', 'provider_result_id', "
                "'title', 'snippet', "
                "stemmer = 'none', stopwords = 'none', overwrite = 1)"
            )
            rebuilt = True
        if _table_exists(connection, "query_variants"):
            connection.execute(
                "PRAGMA create_fts_index("
                "'query_variants', 'variant_id', "
                "'query_text', "
                "stemmer = 'none', stopwords = 'none', overwrite = 1)"
            )
            rebuilt = True
        if rebuilt:
            _ensure_vss_loaded_for_checkpoint(connection)
            connection.execute("CHECKPOINT")
    except Exception:
        # Non-fatal by design: index rebuild must never fail the batch
        # writes that triggered it.  Logged loudly because a persistent
        # failure leaves the BM25 search paths stale or missing.
        logger.warning("FTS index refresh failed", exc_info=True)


def _wal_archive_name(db_file: Path) -> str:
    """Timestamped archive name for a WAL whose replay cannot succeed."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{db_file.name}.unreplayable-{stamp}"


def repair_fts_wal(db_path: str | None = None) -> bool:
    """Recover a database whose WAL cannot be replayed because of FTS DDL.

    Probes a read-only open of the database file.  When that open fails
    with the FTS WAL-replay signature, the WAL file is renamed to
    ``<database>.wal.unreplayable-<timestamp>`` next to the database —
    the bytes are preserved for manual analysis, but DuckDB can never
    replay them, so every later open would fail without this.  Indexes
    are then rebuilt fresh and checkpointed on a write connection.

    Args:
        db_path: DuckDB file path; falls back to the configured
            analytics database when omitted.

    Returns:
        True when an unreplayable WAL was archived and the indexes were
        rebuilt, False when the database opened cleanly.
    """
    db_file = Path(db_path or settings.analytics_duckdb_path)
    try:
        duckdb.connect(str(db_file), read_only=True).close()
        return False
    except Exception as exc:
        message = str(exc)
        if _WAL_REPLAY_ERROR_MARKER not in message or _FTS_SCHEMA_MARKER not in message:
            raise
        logger.warning(
            "FTS WAL replay failed for %s; archiving the WAL so the "
            "database can open. Uncheckpointed writes in the WAL are "
            "not recoverable by DuckDB and are preserved only as bytes.",
            db_file,
        )
    wal_file = Path(f"{db_file}.wal")
    if wal_file.exists():
        wal_file.rename(db_file.parent / _wal_archive_name(db_file))
    connection = duckdb.connect(str(db_file))
    try:
        ensure_fts_loaded(connection)
        refresh_fts_indexes(connection)
    finally:
        connection.close()
    return True


# ---------------------------------------------------------------------------
# BM25 read queries
# ---------------------------------------------------------------------------

_BM25_PROVIDER_RESULTS_SQL = """
SELECT
    provider_result_id,
    run_key,
    provider,
    raw_url,
    title,
    snippet,
    score
FROM (
    SELECT
        pr.*,
        fts_main_provider_results.match_bm25(
            pr.provider_result_id,
            ?,
            fields := NULL
        ) AS score
    FROM provider_results AS pr
) AS scored
WHERE score IS NOT NULL
ORDER BY score DESC
LIMIT ?
"""


def bm25_search_provider_results(
    connection: duckdb.DuckDBPyConnection,
    query_text: str,
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Full-text BM25 search over ``provider_results``.

    Args:
        connection: DuckDB connection with FTS loaded and indexes built.
        query_text: BM25 search string.
        limit: Maximum results to return.

    Returns:
        List of dicts with provider_result_id, run_key, provider,
        raw_url, title, snippet, score.
    """
    rows = connection.execute(_BM25_PROVIDER_RESULTS_SQL, [query_text, limit]).fetchall()
    columns = ["provider_result_id", "run_key", "provider", "raw_url", "title", "snippet", "score"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


_BM25_QUERY_VARIANTS_SQL = """
SELECT
    variant_id,
    run_key,
    variant_role,
    query_text,
    score
FROM (
    SELECT
        qv.*,
        fts_main_query_variants.match_bm25(
            qv.variant_id,
            ?,
            fields := NULL
        ) AS score
    FROM query_variants AS qv
) AS scored
WHERE score IS NOT NULL
ORDER BY score DESC
LIMIT ?
"""


def bm25_search_query_variants(
    connection: duckdb.DuckDBPyConnection,
    query_text: str,
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Full-text BM25 search over ``query_variants``.

    Args:
        connection: DuckDB connection with FTS loaded and indexes built.
        query_text: BM25 search string.
        limit: Maximum results to return.

    Returns:
        List of dicts with variant_id, run_key, variant_role,
        query_text, score.
    """
    rows = connection.execute(_BM25_QUERY_VARIANTS_SQL, [query_text, limit]).fetchall()
    columns = ["variant_id", "run_key", "variant_role", "query_text", "score"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


__all__ = [
    "bm25_search_provider_results",
    "bm25_search_query_variants",
    "ensure_fts_loaded",
    "refresh_fts_indexes",
    "repair_fts_wal",
]
