from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestQueryRewritesSchema:
    """Test the query_rewrites table schema, write path, and round-trip."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_query_rewrites,
            insert_query_rewrites,
        )

        db_path = Path("test_query_rewrites_round_trip.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            con = duckdb.connect(str(db_path))
            _ensure_query_rewrites(con)
            con.close()

            insert_query_rewrites(
                run_key="run-003",
                variant_index=0,
                branch_type="core",
                kind="academic",
                target="arxiv",
                query="FastMCP Python SDK",
                weight=1.0,
                reason="primary search intent",
                max_results=10,
                model="cerebras/gpt-oss-120b",
                duration_ms=123.4,
                payload_json=json.dumps({"rewriter": "groq"}),
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                """
                SELECT
                    run_key, variant_index, branch_type, kind, target,
                    query, weight, reason, max_results, model, duration_ms, payload_json
                FROM query_rewrites
                """
            ).fetchone()
            con.close()

            assert row is not None, "Expected a row in query_rewrites"
            assert row[0] == "run-003"
            assert row[1] == 0
            assert row[2] == "core"
            assert row[3] == "academic"
            assert row[4] == "arxiv"
            assert row[5] == "FastMCP Python SDK"
            assert row[6] == 1.0
            assert row[7] == "primary search intent"
            assert row[8] == 10
            assert row[9] == "cerebras/gpt-oss-120b"
            assert row[10] == 123.4
            assert json.loads(row[11]) == {"rewriter": "groq"}

        finally:
            if db_path.exists():
                db_path.unlink()

    def test_index_exists(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_query_rewrites,
        )

        db_path = Path("test_query_rewrites_index.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            con = duckdb.connect(str(db_path))
            _ensure_query_rewrites(con)

            indexes = set()
            for row in con.execute(
                "SELECT index_name FROM duckdb_indexes WHERE table_name = 'query_rewrites'"
            ).fetchall():
                indexes.add(row[0])
            con.close()

            assert "idx_qr_run_key" in indexes

        finally:
            if db_path.exists():
                db_path.unlink()

    def test_defaults_on_insert(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_query_rewrites,
            insert_query_rewrites,
        )

        db_path = Path("test_query_rewrites_defaults.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            con = duckdb.connect(str(db_path))
            _ensure_query_rewrites(con)
            con.close()

            insert_query_rewrites(
                run_key="run-defaults",
                query="minimal",
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                """
                SELECT
                    run_key, query, recorded_at::text,
                    variant_index, branch_type, kind, target,
                    weight, reason, max_results, model, duration_ms, payload_json
                FROM query_rewrites
                """
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-defaults"
            assert row[1] == "minimal"
            assert row[2] is not None  # recorded_at
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
