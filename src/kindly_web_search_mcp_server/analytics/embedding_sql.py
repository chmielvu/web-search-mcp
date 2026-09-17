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


__all__ = [
    "_build_embedding_similarity_view_sql",
    "_ensure_embedding_similarity_view",
]
