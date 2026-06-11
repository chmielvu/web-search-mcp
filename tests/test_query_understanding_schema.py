from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestQueryUnderstandingSchema:
    """Test the query_understanding table schema, write path, and round-trip."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_query_understanding,
            insert_query_understanding,
        )

        db_path = Path("test_query_understanding_round_trip.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            con = duckdb.connect(str(db_path))
            _ensure_query_understanding(con)
            con.close()

            insert_query_understanding(
                run_key="run-002",
                intent="research",
                confidence=0.92,
                should_decompose=True,
                rationale="Complex query with multiple sub-questions",
                model="gpt-4o",
                provider="openai",
                duration_ms=456.7,
                fallback_used=False,
                entities_count=3,
                preserved_terms=["FastMCP", "Python", "SDK"],
                time_sensitivity="medium",
                payload_json=json.dumps({"entities": [{"text": "FastMCP", "label": "TECH"}]}),
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                """
                SELECT
                    run_key, intent, confidence, should_decompose,
                    rationale, model, provider, duration_ms,
                    fallback_used, entities_count, preserved_terms,
                    time_sensitivity, payload_json
                FROM query_understanding
                """
            ).fetchone()
            con.close()

            assert row is not None, "Expected a row in query_understanding"
            assert row[0] == "run-002"
            assert row[1] == "research"
            assert row[2] == 0.92
            assert row[3] is True
            assert row[4] == "Complex query with multiple sub-questions"
            assert row[5] == "gpt-4o"
            assert row[6] == "openai"
            assert row[7] == 456.7
            assert row[8] is False
            assert row[9] == 3
            assert row[10] == ["FastMCP", "Python", "SDK"]
            assert row[11] == "medium"
            assert json.loads(row[12]) == {"entities": [{"text": "FastMCP", "label": "TECH"}]}

        finally:
            if db_path.exists():
                db_path.unlink()

    def test_index_exists(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_query_understanding,
        )

        db_path = Path("test_query_understanding_index.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            con = duckdb.connect(str(db_path))
            _ensure_query_understanding(con)

            indexes = set()
            for row in con.execute(
                "SELECT index_name FROM duckdb_indexes WHERE table_name = 'query_understanding'"
            ).fetchall():
                indexes.add(row[0])
            con.close()

            assert "idx_qu_run_key" in indexes

        finally:
            if db_path.exists():
                db_path.unlink()

    def test_defaults_on_insert(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_query_understanding,
            insert_query_understanding,
        )

        db_path = Path("test_query_understanding_defaults.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            con = duckdb.connect(str(db_path))
            _ensure_query_understanding(con)
            con.close()

            insert_query_understanding(
                run_key="run-defaults",
                intent="factual",
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                """
                SELECT
                    run_key, intent, confidence, should_decompose,
                    rationale, model, provider, duration_ms,
                    fallback_used, entities_count, preserved_terms,
                    time_sensitivity, payload_json
                FROM query_understanding
                """
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-defaults"
            assert row[1] == "factual"
            assert row[2] is None
            assert row[3] is None
            assert row[4] is None
            assert row[5] is None
            assert row[6] is None
            assert row[7] is None
            assert row[8] is None
            assert row[9] is None
            assert row[10] is None
            assert row[11] is None
            assert row[12] is None

        finally:
            if db_path.exists():
                db_path.unlink()
