from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestABSchema:
    """Test A/B testing schema tables (ab_experiments, ab_shadow_runs)."""

    # ------------------------------------------------------------------
    # ab_experiments table
    # ------------------------------------------------------------------

    def test_ab_experiments_table_columns_and_types(self) -> None:
        """Verify all expected columns exist with correct types on ab_experiments."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_ab_experiments,
        )

        db_path = Path("test_abe_cols.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_ab_experiments(con)
            cols = {
                r[1]: r[2]
                for r in con.execute("PRAGMA table_info('ab_experiments')").fetchall()
            }
            con.close()

            expected = {
                "experiment_id": "VARCHAR",
                "created_at": "TIMESTAMP WITH TIME ZONE",
                "layer": "VARCHAR",
                "variant_a": "VARCHAR",
                "variant_b": "VARCHAR",
                "allocation_rate": "DOUBLE",
                "status": "VARCHAR",
                "start_date": "DATE",
                "end_date": "DATE",
                "min_sample_size": "INTEGER",
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

    def test_ab_experiments_insert_round_trip(self) -> None:
        """Insert an ab_experiment and read it back."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_ab_experiments,
            insert_ab_experiment,
        )

        db_path = Path("test_abe_rt.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_ab_experiments(con)
            con.close()

            insert_ab_experiment(
                experiment_id="exp-001",
                layer="search_ranking",
                variant_a="control",
                variant_b="neural_v1",
                allocation_rate=0.5,
                status="active",
                start_date="2025-01-01",
                end_date="2025-02-01",
                min_sample_size=1000,
                payload_json=json.dumps({"feature": "bert_rerank"}),
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT experiment_id, layer, variant_a, variant_b, "
                "allocation_rate, status, start_date, end_date, "
                "min_sample_size, payload_json FROM ab_experiments"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "exp-001"
            assert row[1] == "search_ranking"
            assert row[2] == "control"
            assert row[3] == "neural_v1"
            assert row[4] == 0.5
            assert row[5] == "active"
            assert str(row[6]) == "2025-01-01"
            assert str(row[7]) == "2025-02-01"
            assert row[8] == 1000
            assert json.loads(row[9]) == {"feature": "bert_rerank"}
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_ab_experiments_primary_key_on_conflict_do_nothing(self) -> None:
        """Duplicate experiment_id is silently ignored via ON CONFLICT DO NOTHING."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_ab_experiments,
            insert_ab_experiment,
        )

        db_path = Path("test_abe_pk.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_ab_experiments(con)
            con.close()

            # First insert
            insert_ab_experiment(
                experiment_id="exp-pk",
                layer="layer_a",
                variant_a="ctrl",
                variant_b="test",
                allocation_rate=0.5,
                status="active",
                payload_json=None,
                db_path=str(db_path),
            )
            # Second insert with same experiment_id — should be silently ignored
            insert_ab_experiment(
                experiment_id="exp-pk",
                layer="layer_b",
                variant_a="old",
                variant_b="new",
                allocation_rate=0.3,
                status="inactive",
                payload_json=None,
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            rows = con.execute(
                "SELECT experiment_id, layer FROM ab_experiments"
            ).fetchall()
            con.close()

            assert len(rows) == 1, (
                f"Expected 1 row after ON CONFLICT DO NOTHING, got {len(rows)}"
            )
            # Original values must be preserved (first insert won, not second)
            assert rows[0][0] == "exp-pk"
            assert rows[0][1] == "layer_a"
        finally:
            if db_path.exists():
                db_path.unlink()

    # ------------------------------------------------------------------
    # ab_shadow_runs table
    # ------------------------------------------------------------------

    def test_ab_shadow_runs_table_columns_and_types(self) -> None:
        """Verify all expected columns exist with correct types on ab_shadow_runs."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_ab_shadow_runs,
        )

        db_path = Path("test_abs_cols.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_ab_shadow_runs(con)
            cols = {
                r[1]: r[2]
                for r in con.execute(
                    "PRAGMA table_info('ab_shadow_runs')"
                ).fetchall()
            }
            con.close()

            expected = {
                "run_key": "VARCHAR",
                "recorded_at": "TIMESTAMP WITH TIME ZONE",
                "experiment_id": "VARCHAR",
                "variant": "VARCHAR",
                "layer": "VARCHAR",
                "duration_ms": "DOUBLE",
                "judge_score": "DOUBLE",
                "tokens_used": "INTEGER",
                "cost_usd": "DOUBLE",
                "error_type": "VARCHAR",
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

    def test_ab_shadow_runs_insert_round_trip(self) -> None:
        """Insert an ab_shadow_run and read it back."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_ab_shadow_runs,
            insert_ab_shadow_run,
        )

        db_path = Path("test_abs_rt.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_ab_shadow_runs(con)
            con.close()

            insert_ab_shadow_run(
                run_key="shadow-001",
                experiment_id="exp-001",
                variant="control",
                layer="search_ranking",
                duration_ms=1234.5,
                judge_score=0.92,
                tokens_used=1500,
                cost_usd=0.0032,
                error_type=None,
                payload_json=json.dumps({"query": "test query"}),
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT run_key, experiment_id, variant, layer, "
                "duration_ms, judge_score, tokens_used, cost_usd, "
                "error_type, payload_json FROM ab_shadow_runs"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "shadow-001"
            assert row[1] == "exp-001"
            assert row[2] == "control"
            assert row[3] == "search_ranking"
            assert row[4] == 1234.5
            assert row[5] == 0.92
            assert row[6] == 1500
            assert row[7] == 0.0032
            assert row[8] is None
            assert json.loads(row[9]) == {"query": "test query"}
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_multiple_shadow_runs_same_experiment_id(self) -> None:
        """Multiple shadow runs for the same experiment_id must be allowed."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_ab_shadow_runs,
            insert_ab_shadow_run,
        )

        db_path = Path("test_abs_multi.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_ab_shadow_runs(con)
            con.close()

            # Insert two shadow runs for the same experiment_id
            insert_ab_shadow_run(
                run_key="shadow-a",
                experiment_id="exp-multi",
                variant="control",
                layer="search",
                duration_ms=100.0,
                judge_score=0.8,
                tokens_used=500,
                cost_usd=0.001,
                error_type=None,
                payload_json=None,
                db_path=str(db_path),
            )
            insert_ab_shadow_run(
                run_key="shadow-b",
                experiment_id="exp-multi",
                variant="neural_v1",
                layer="search",
                duration_ms=200.0,
                judge_score=0.9,
                tokens_used=800,
                cost_usd=0.002,
                error_type=None,
                payload_json=None,
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            rows = con.execute(
                "SELECT run_key, experiment_id, variant, judge_score "
                "FROM ab_shadow_runs WHERE experiment_id = 'exp-multi' "
                "ORDER BY run_key"
            ).fetchall()
            con.close()

            assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
            assert rows[0][0] == "shadow-a"
            assert rows[0][1] == "exp-multi"
            assert rows[0][2] == "control"
            assert rows[1][0] == "shadow-b"
            assert rows[1][1] == "exp-multi"
            assert rows[1][2] == "neural_v1"
        finally:
            if db_path.exists():
                db_path.unlink()

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------

    def test_ab_shadow_runs_indexes_exist(self) -> None:
        """Verify idx_abs_run_key and idx_abs_exp indexes exist."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_ab_shadow_runs,
        )

        db_path = Path("test_abs_idx.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_ab_shadow_runs(con)
            indexes = {
                r[0]
                for r in con.execute(
                    "SELECT index_name FROM duckdb_indexes "
                    "WHERE table_name = 'ab_shadow_runs'"
                ).fetchall()
            }
            con.close()
            assert "idx_abs_run_key" in indexes, (
                f"Missing idx_abs_run_key; found indexes: {indexes}"
            )
            assert "idx_abs_exp" in indexes, (
                f"Missing idx_abs_exp; found indexes: {indexes}"
            )
        finally:
            if db_path.exists():
                db_path.unlink()