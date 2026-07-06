from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestSearchRunsSchema:
    """Test the search_runs table schema, write path, and round-trip."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_search_runs,
            insert_search_run,
        )

        db_path = Path("test_search_runs_round_trip.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            con = duckdb.connect(str(db_path))
            _ensure_search_runs(con)
            con.close()

            insert_search_run(
                run_key="run-001",
                query="FastMCP Python SDK",
                normalized_query="fastmcp python sdk",
                research_goal="understand FastMCP API",
                num_results_requested=10,
                rewrite_enabled=True,
                session_id="sess-abc",
                tool_name="web_search",
                duration_ms=1234.5,
                final_result_count=8,
                candidate_count=15,
                has_more=False,
                result_offset=0,
                status="success",
                error_type=None,
                payload_json=json.dumps({"sources": ["searxng", "brave"]}),
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                """
                SELECT
                    run_key,
                    query,
                    normalized_query,
                    research_goal,
                    num_results_requested,
                    rewrite_enabled,
                    session_id,
                    tool_name,
                    duration_ms,
                    final_result_count,
                    candidate_count,
                    has_more,
                    result_offset,
                    status,
                    error_type,
                    payload_json
                FROM search_runs
                """
            ).fetchone()
            con.close()

            assert row is not None, "Expected a row in search_runs"
            assert row[0] == "run-001"
            assert row[1] == "FastMCP Python SDK"
            assert row[2] == "fastmcp python sdk"
            assert row[3] == "understand FastMCP API"
            assert row[4] == 10
            assert row[5] is True
            assert row[6] == "sess-abc"
            assert row[7] == "web_search"
            assert row[8] == 1234.5
            assert row[9] == 8
            assert row[10] == 15
            assert row[11] is False
            assert row[12] == 0
            assert row[13] == "success"
            assert row[14] is None
            assert json.loads(row[15]) == {"sources": ["searxng", "brave"]}

        finally:
            if db_path.exists():
                db_path.unlink()

    def test_indexes_exist(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_search_runs,
        )

        db_path = Path("test_search_runs_indexes.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            con = duckdb.connect(str(db_path))
            _ensure_search_runs(con)

            # duckdb_indexes columns: table_catalog, table_schema, table_name, index_name, ...
            indexes = set()
            for row in con.execute(
                "SELECT index_name FROM duckdb_indexes WHERE table_name = 'search_runs'"
            ).fetchall():
                indexes.add(row[0])
            con.close()

            assert "idx_runs_run_key" in indexes
            assert "idx_runs_recorded_at" in indexes

        finally:
            if db_path.exists():
                db_path.unlink()

    def test_defaults_on_insert(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_search_runs,
            insert_search_run,
        )

        db_path = Path("test_search_runs_defaults.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            con = duckdb.connect(str(db_path))
            _ensure_search_runs(con)
            con.close()

            # Minimal insert — only required fields
            insert_search_run(
                run_key="run-defaults",
                query="minimal test",
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                """
                SELECT
                    run_key,
                    query,
                    tool_name,
                    recorded_at::text,
                    duration_ms,
                    final_result_count,
                    candidate_count,
                    has_more,
                    result_offset,
                    status,
                    error_type,
                    payload_json
                FROM search_runs
                """
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-defaults"
            assert row[1] == "minimal test"
            assert row[2] == "web_search"  # DEFAULT 'web_search'
            assert row[3] is not None  # recorded_at should be populated
            assert row[4] is None  # duration_ms defaults to NULL
            assert row[5] is None
            assert row[6] is None
            assert row[7] is None
            assert row[8] is None
            assert row[9] is None
            assert row[10] is None
            assert row[11] is None

        finally:
            if db_path.exists():
                db_path.unlink()

    def test_recorded_at_is_timestamptz_with_default_now(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_search_runs,
            insert_search_run,
        )

        db_path = Path("test_search_runs_recorded_at.duckdb")
        if db_path.exists():
            db_path.unlink()

        try:
            con = duckdb.connect(str(db_path))
            _ensure_search_runs(con)
            con.close()

            insert_search_run(
                run_key="run-ts",
                query="timestamp test",
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT recorded_at::text FROM search_runs WHERE run_key = 'run-ts'"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] is not None, "recorded_at should be populated by DEFAULT now()"

        finally:
            if db_path.exists():
                db_path.unlink()
