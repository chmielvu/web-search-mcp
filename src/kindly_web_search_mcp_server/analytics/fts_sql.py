"""DuckDB FTS (full-text search) bootstrap and BM25 read helpers.

Provides index creation on ``provider_results`` (title, snippet) and
``query_variants`` (query_text), plus parameterised BM25 search queries
against those tables.  FTS indexes are **not** auto-updated when source
rows change — call ``refresh_fts_indexes`` after batch writes.
"""

from __future__ import annotations

import logging

import duckdb

logger = logging.getLogger(__name__)

_FTS_INSTALLED: bool = False


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


def refresh_fts_indexes(connection: duckdb.DuckDBPyConnection) -> None:
    """Rebuild FTS indexes on ``provider_results`` and ``query_variants``.

    Must be called after batch writes to those tables because DuckDB FTS
    indexes do not auto-update on INSERT.  Silently no-ops when tables
    do not exist or FTS is not loaded.
    """
    try:
        if _table_exists(connection, "provider_results"):
            connection.execute(
                "PRAGMA create_fts_index("
                "'provider_results', 'provider_result_id', "
                "'title', 'snippet', "
                "stemmer = 'none', stopwords = 'none', overwrite = 1)"
            )
        if _table_exists(connection, "query_variants"):
            connection.execute(
                "PRAGMA create_fts_index("
                "'query_variants', 'variant_id', "
                "'query_text', "
                "stemmer = 'none', stopwords = 'none', overwrite = 1)"
            )
    except Exception:
        logger.debug("FTS index refresh failed", exc_info=True)


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
]
