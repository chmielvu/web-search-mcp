from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestJudgeEvaluationsSchema:
    """Test judge_evaluations table."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_judge_evaluations,
            insert_judge_evaluation,
        )

        db_path = Path("test_judge_evaluations.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_judge_evaluations(con)
            con.close()

            insert_judge_evaluation(
                run_key="run-je-001",
                tool_name="web_search",
                judge_model="gpt-4o-mini",
                relevance_score=0.95,
                accuracy_score=0.88,
                completeness_score=0.92,
                source_quality_score=0.85,
                overall_score=0.90,
                rationale="Good results overall.",
                duration_ms=1234.5,
                tokens_used=1500,
                cost_usd=0.0032,
                payload_json=json.dumps({"prompt_tokens": 500, "completion_tokens": 1000}),
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT run_key, tool_name, judge_model, relevance_score, accuracy_score, "
                "completeness_score, source_quality_score, overall_score, rationale, "
                "duration_ms, tokens_used, cost_usd, payload_json FROM judge_evaluations"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-je-001"  # run_key
            assert row[1] == "web_search"  # tool_name
            assert row[2] == "gpt-4o-mini"  # judge_model
            assert row[3] == 0.95  # relevance_score
            assert row[4] == 0.88  # accuracy_score
            assert row[5] == 0.92  # completeness_score
            assert row[6] == 0.85  # source_quality_score
            assert row[7] == 0.90  # overall_score
            assert row[8] == "Good results overall."  # rationale
            assert row[9] == 1234.5  # duration_ms
            assert row[10] == 1500  # tokens_used
            assert row[11] == 0.0032  # cost_usd
            assert json.loads(row[12]) == {"prompt_tokens": 500, "completion_tokens": 1000}  # payload_json
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_index_exists(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import _ensure_judge_evaluations

        db_path = Path("test_je_idx.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_judge_evaluations(con)
            indexes = {r[0] for r in con.execute(
                "SELECT index_name FROM duckdb_indexes WHERE table_name = 'judge_evaluations'"
            ).fetchall()}
            con.close()
            assert "idx_je_run_key" in indexes
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_table_columns_match_spec(self) -> None:
        """Verify all expected columns exist with correct types."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import _ensure_judge_evaluations

        db_path = Path("test_je_cols.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_judge_evaluations(con)
            cols = {
                r[1]: r[2]
                for r in con.execute("PRAGMA table_info('judge_evaluations')").fetchall()
            }
            con.close()

            expected = {
                "run_key": "VARCHAR",
                "recorded_at": "TIMESTAMP WITH TIME ZONE",
                "tool_name": "VARCHAR",
                "judge_model": "VARCHAR",
                "relevance_score": "DOUBLE",
                "accuracy_score": "DOUBLE",
                "completeness_score": "DOUBLE",
                "source_quality_score": "DOUBLE",
                "overall_score": "DOUBLE",
                "rationale": "VARCHAR",
                "duration_ms": "DOUBLE",
                "tokens_used": "INTEGER",
                "cost_usd": "DOUBLE",
                "payload_json": "JSON",
            }
            for col, expected_type in expected.items():
                assert col in cols, f"Missing column: {col}"
                assert cols[col] == expected_type, (
                    f"Column '{col}' expected type '{expected_type}', got '{cols[col]}'"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_multiple_evaluations_same_run_key(self) -> None:
        """Multiple evaluations for the same run_key must be allowed (not unique)."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_judge_evaluations,
            insert_judge_evaluation,
        )

        db_path = Path("test_je_multi.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_judge_evaluations(con)
            con.close()

            # Insert two evaluations with the same run_key but different tool/model
            insert_judge_evaluation(
                run_key="run-je-same",
                tool_name="web_search",
                judge_model="gpt-4o-mini",
                relevance_score=0.9,
                accuracy_score=0.8,
                completeness_score=0.85,
                source_quality_score=0.8,
                overall_score=0.84,
                rationale="First eval.",
                duration_ms=100.0,
                tokens_used=500,
                cost_usd=0.001,
                payload_json=None,
                db_path=str(db_path),
            )
            insert_judge_evaluation(
                run_key="run-je-same",
                tool_name="code_interpreter",
                judge_model="claude-3-haiku",
                relevance_score=0.7,
                accuracy_score=0.9,
                completeness_score=0.6,
                source_quality_score=0.75,
                overall_score=0.74,
                rationale="Second eval.",
                duration_ms=200.0,
                tokens_used=800,
                cost_usd=0.002,
                payload_json=None,
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            rows = con.execute(
                "SELECT run_key, tool_name, judge_model, overall_score FROM judge_evaluations "
                "WHERE run_key = 'run-je-same' ORDER BY overall_score"
            ).fetchall()
            con.close()

            assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
            assert rows[0][0] == "run-je-same"
            assert rows[0][1] == "code_interpreter"
            assert rows[1][0] == "run-je-same"
            assert rows[1][1] == "web_search"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_all_columns_populated(self) -> None:
        """All columns are populated correctly with non-null values."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_judge_evaluations,
            insert_judge_evaluation,
        )

        db_path = Path("test_je_allcols.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_judge_evaluations(con)
            con.close()

            insert_judge_evaluation(
                run_key="run-je-full",
                tool_name="web_search",
                judge_model="gpt-4o",
                relevance_score=0.99,
                accuracy_score=0.97,
                completeness_score=0.96,
                source_quality_score=0.95,
                overall_score=0.97,
                rationale="Excellent results across all dimensions.",
                duration_ms=4567.89,
                tokens_used=2345,
                cost_usd=0.0089,
                payload_json=json.dumps({"model": "gpt-4o-2024-08-06", "temperature": 0.3}),
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute("SELECT * FROM judge_evaluations").fetchone()
            con.close()

            assert row is not None
            # row[0]: run_key
            assert row[0] == "run-je-full"
            # row[1]: recorded_at — should be auto-populated (not None)
            assert row[1] is not None, "recorded_at should be auto-populated"
            # row[2]: tool_name
            assert row[2] == "web_search"
            # row[3]: judge_model
            assert row[3] == "gpt-4o"
            # row[4]: relevance_score
            assert row[4] == 0.99
            # row[5]: accuracy_score
            assert row[5] == 0.97
            # row[6]: completeness_score
            assert row[6] == 0.96
            # row[7]: source_quality_score
            assert row[7] == 0.95
            # row[8]: overall_score
            assert row[8] == 0.97
            # row[9]: rationale
            assert row[9] == "Excellent results across all dimensions."
            # row[10]: duration_ms
            assert row[10] == 4567.89
            # row[11]: tokens_used
            assert row[11] == 2345
            # row[12]: cost_usd
            assert row[12] == 0.0089
            # row[13]: payload_json
            assert json.loads(row[13]) == {"model": "gpt-4o-2024-08-06", "temperature": 0.3}
        finally:
            if db_path.exists():
                db_path.unlink()