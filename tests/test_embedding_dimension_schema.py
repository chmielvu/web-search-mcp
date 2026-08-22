"""Regression coverage for the 786-dimensional embedding storage contract."""

from __future__ import annotations

import duckdb

from kindly_web_search_mcp_server.analytics.async_writes import drain_duckdb_writes
from kindly_web_search_mcp_server.analytics.duckdb_store import (
    ensure_store_schema,
    insert_candidate_embeddings,
    insert_query_embeddings,
)


def _single_value(connection: duckdb.DuckDBPyConnection, sql: str) -> object:
    row = connection.execute(sql).fetchone()
    assert row is not None
    return row[0]


def _embedding_type(connection: duckdb.DuckDBPyConnection, table_name: str) -> str:
    value = _single_value(
        connection,
        f"SELECT column_type FROM (DESCRIBE {table_name}) WHERE column_name = 'embedding'",
    )
    assert isinstance(value, str)
    return value


def test_embedding_schema_rolls_over_legacy_vectors_and_persists_786d_rows(tmp_path) -> None:
    db_path = tmp_path / "analytics.duckdb"
    legacy_vector = [0.0] * 1024
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            "CREATE TABLE query_embeddings (run_key VARCHAR NOT NULL, embedding FLOAT[1024])"
        )
        connection.execute(
            "CREATE TABLE candidate_embeddings (run_key VARCHAR NOT NULL, link VARCHAR, embedding FLOAT[1024])"
        )
        connection.execute(
            "INSERT INTO query_embeddings VALUES ('legacy-query', ?)", [legacy_vector]
        )
        connection.execute(
            "INSERT INTO candidate_embeddings VALUES ('legacy-candidate', 'https://example.test', ?)",
            [legacy_vector],
        )
    finally:
        connection.close()

    ensure_store_schema(db_path=str(db_path))
    vector = [0.0] * 786
    insert_query_embeddings(
        run_key="query-786",
        embedding=vector,
        model_id="configured-786d-model",
        payload_json=None,
        db_path=str(db_path),
    )
    insert_candidate_embeddings(
        run_key="query-786",
        link="https://example.test/786",
        title="786 dimensional candidate",
        embedding=vector,
        model_id="configured-786d-model",
        payload_json=None,
        db_path=str(db_path),
    )
    drain_duckdb_writes()

    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        assert _embedding_type(connection, "query_embeddings") == "FLOAT[786]"
        assert _embedding_type(connection, "candidate_embeddings") == "FLOAT[786]"
        assert _single_value(connection, "SELECT count() FROM query_embeddings_1024d_legacy") == 1
        assert (
            _single_value(connection, "SELECT count() FROM candidate_embeddings_1024d_legacy") == 1
        )
        assert (
            _single_value(
                connection,
                "SELECT array_length(embedding) FROM query_embeddings WHERE run_key = 'query-786'",
            )
            == 786
        )
        assert (
            _single_value(
                connection,
                "SELECT array_length(embedding) FROM candidate_embeddings WHERE run_key = 'query-786'",
            )
            == 786
        )
    finally:
        connection.close()
