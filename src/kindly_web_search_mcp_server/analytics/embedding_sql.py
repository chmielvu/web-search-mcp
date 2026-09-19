"""SQL and bootstrap helpers for embedding similarity analytics views."""

from __future__ import annotations

import duckdb

_EMBEDDING_SIMILARITY_TABLES = frozenset(
    {"candidate_embeddings", "final_results", "query_embeddings"}
)


def _build_embedding_similarity_view_sql(target: str) -> str:
    """Return SQL for the query-to-candidate embedding similarity view."""
    return f"""
        CREATE OR REPLACE VIEW {target}.vw_embedding_similarity AS
        SELECT
            qe.run_key,
            ce.link,
            ce.title,
            array_cosine_distance(qe.embedding, ce.embedding) AS cosine_distance,
            (1 - array_cosine_distance(qe.embedding, ce.embedding)) AS cosine_similarity,
            fr.rank AS final_rank,
            fr.final_score AS final_score,
            qe.model_id AS query_model_id,
            ce.model_id AS candidate_model_id
        FROM {target}.query_embeddings AS qe
        INNER JOIN {target}.candidate_embeddings AS ce ON (qe.run_key = ce.run_key)
        LEFT JOIN {target}.final_results AS fr
            ON (ce.run_key = fr.run_key AND ce.link = fr.link)
        WHERE qe.embedding IS NOT NULL AND ce.embedding IS NOT NULL
        ORDER BY qe.run_key, cosine_distance
    """


def _ensure_embedding_similarity_view(connection: duckdb.DuckDBPyConnection) -> None:
    """Recreate the embedding view when all of its source tables exist."""
    tables = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name IN (?, ?, ?)
            """,
            tuple(sorted(_EMBEDDING_SIMILARITY_TABLES)),
        ).fetchall()
    }
    if tables == _EMBEDDING_SIMILARITY_TABLES:
        connection.execute(_build_embedding_similarity_view_sql("main"))


_KNN_CANDIDATE_SQL = """
    WITH nearest AS (
        SELECT
            ce.run_key,
            ce.link,
            ce.title,
            array_cosine_distance(ce.embedding, ?::FLOAT[384]) AS cosine_distance
        FROM candidate_embeddings AS ce
        WHERE ce.embedding IS NOT NULL
        ORDER BY cosine_distance
        LIMIT ?
    )
    SELECT
        nearest.run_key,
        nearest.link,
        nearest.title,
        nearest.cosine_distance,
        fr.rank AS final_rank,
        fr.final_score
    FROM nearest
    LEFT JOIN final_results AS fr
      ON fr.run_key = nearest.run_key
     AND fr.link = nearest.link
    ORDER BY nearest.cosine_distance
"""


def knn_candidates(
    connection: duckdb.DuckDBPyConnection,
    query_embedding: list[float],
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Return the nearest candidate embeddings via HNSW KNN.

    Uses the ``idx_cemb_hnsw`` index (cosine metric) to find the
    ``limit`` closest candidates to ``query_embedding``.  Joins
    ``final_results`` for rank/score context when available.

    Args:
        connection: Read-write DuckDB connection with vss loaded.
        query_embedding: 384-dimensional query vector.
        limit: Maximum neighbors to return.

    Returns:
        List of dicts with keys: run_key, link, title,
        cosine_distance, final_rank, final_score.
    """
    rows = connection.execute(_KNN_CANDIDATE_SQL, [query_embedding, limit]).fetchall()
    columns = ["run_key", "link", "title", "cosine_distance", "final_rank", "final_score"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


__all__ = [
    "_build_embedding_similarity_view_sql",
    "_ensure_embedding_similarity_view",
    "knn_candidates",
]
