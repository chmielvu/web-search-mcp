"""Tests for direct DuckDB read and SQLite graph generation commands."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path

import duckdb

from kindly_web_search_mcp_server.analytics.duckdb_store import ensure_store_schema
from kindly_web_search_mcp_server.analytics.graph_feedback import main
from kindly_web_search_mcp_server.analytics.writers.core import insert_result_labels


def _seed_graph_source(db_path: str, recorded_at: datetime) -> None:
    connection = duckdb.connect(db_path, read_only=False)
    try:
        connection.execute(
            """
            INSERT INTO search_runs (run_key, query, normalized_query, recorded_at) VALUES
                ('run-one', 'source query', 'source query', ?),
                ('run-two', 'related query', 'related query', ?)
            """,
            [recorded_at, recorded_at],
        )
        connection.execute(
            """
            INSERT INTO final_results (
                run_key, rank, link, canonical_result_id, recorded_at
            )
            VALUES
                ('run-one', 1, 'https://example.com/document-one', 'document-one', ?),
                ('run-one', 2, 'https://example.com/document-two', 'document-two', ?),
                ('run-two', 1, 'https://example.com/document-one', 'document-one', ?),
                ('run-two', 2, 'https://example.com/document-two', 'document-two', ?)
            """,
            [recorded_at] * 4,
        )
    finally:
        connection.close()

    insert_result_labels(
        [
            {
                "run_key": run_key,
                "position": position,
                "label": 1.0,
                "canonical_result_id": document_id,
                "raw_url": f"https://example.com/{document_id}",
                "source": "llm_judge",
                "rubric_version": "v1",
                "recorded_at": recorded_at,
            }
            for run_key, position, document_id in (
                ("run-one", 0, "document-one"),
                ("run-one", 1, "document-two"),
                ("run-two", 0, "document-one"),
                ("run-two", 1, "document-two"),
            )
        ],
        db_path=db_path,
        sync=True,
    )


def test_generate_and_compare_commands_write_sqlite_only(tmp_path: Path) -> None:
    db_path = tmp_path / "analytics.duckdb"
    sqlite_path = tmp_path / "graph.sqlite"
    sqlite_dir = tmp_path / "windows"
    ensure_store_schema(db_path=str(db_path))
    cutoff = datetime.now(timezone.utc)
    _seed_graph_source(str(db_path), cutoff)

    generated_output = io.StringIO()
    with redirect_stdout(generated_output):
        assert (
            main(
                [
                    "generate",
                    "--db-path",
                    str(db_path),
                    "--sqlite-path",
                    str(sqlite_path),
                    "--cutoff",
                    cutoff.isoformat(),
                ]
            )
            == 0
        )
    generated = json.loads(generated_output.getvalue())
    assert generated["query_node_count"] == 2
    assert generated["document_node_count"] == 2
    assert generated["neighbor_row_count"] == 2
    assert sqlite_path.exists()

    compared_output = io.StringIO()
    with redirect_stdout(compared_output):
        assert (
            main(
                [
                    "compare",
                    "--db-path",
                    str(db_path),
                    "--sqlite-dir",
                    str(sqlite_dir),
                    "--cutoff",
                    cutoff.isoformat(),
                    "--windows",
                    "30,60,90",
                ]
            )
            == 0
        )
    compared = json.loads(compared_output.getvalue())
    assert [item["lookback_days"] for item in compared["windows"]] == [30, 60, 90]
    assert all(Path(item["sqlite_path"]).exists() for item in compared["windows"])

    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        graph_tables = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name LIKE 'graph_%'
            """
        ).fetchall()
    finally:
        connection.close()
    assert graph_tables == []
