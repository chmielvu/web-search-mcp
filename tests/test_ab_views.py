from __future__ import annotations

import duckdb
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestABViews:
    """Test A/B analytics views with sample data."""

    def _setup_db(self, db_path: Path) -> None:
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_ab_assignments,
            _ensure_ab_experiment_variants,
            _ensure_ab_experiments,
            _ensure_ab_results,
            _ensure_ab_shadow_runs,
        )
        from kindly_web_search_mcp_server.analytics.views import ensure_views
        from kindly_web_search_mcp_server.analytics.writers.schema import (
            ensure_store_schema,
        )

        # Create the 16 base pipeline tables (canonical aggregator).
        # The 2026-07-20 schema consolidation removed query_understanding,
        # query_rewrites, provider_candidates, and merged_candidates — they
        # are no longer referenced from this aggregator.
        ensure_store_schema(db_path=str(db_path))

        # The 5 A/B-specific tables live in a separate schema writer
        # (analytics/writers/ab_schema.py) and are NOT created by
        # `ensure_store_schema`. Create them explicitly via the surviving
        # duckdb_store re-exports.
        con = duckdb.connect(str(db_path))
        try:
            _ensure_ab_experiments(con)
            _ensure_ab_experiment_variants(con)
            _ensure_ab_assignments(con)
            _ensure_ab_results(con)
            _ensure_ab_shadow_runs(con)
        finally:
            con.close()
        # Insert sample data
        con = duckdb.connect(str(db_path))

        # Experiment
        con.execute("""
            INSERT INTO ab_experiments (experiment_id, layer, variant_a, variant_b, allocation_rate, status, start_date, end_date, min_sample_size)
            VALUES ('exp-ranking-001', 'search_ranking', 'control', 'neural_v1', 0.5, 'active', '2025-01-01', '2025-02-01', 1000)
        """)

        # Variants
        con.execute("""
            INSERT INTO ab_experiment_variants (variant_id, experiment_id, variant_name, description, config_json)
            VALUES ('var-ctrl-001', 'exp-ranking-001', 'control', 'Current production ranking', '{"model": "bm25"}'),
                   ('var-test-001', 'exp-ranking-001', 'neural_v1', 'Neural reranking v1', '{"model": "bert"}')
        """)

        # Assignments
        con.execute("""
            INSERT INTO ab_assignments (assignment_id, experiment_id, run_key, variant)
            VALUES ('asn-001', 'exp-ranking-001', 'run-001', 'control'),
                   ('asn-002', 'exp-ranking-001', 'run-002', 'control'),
                   ('asn-003', 'exp-ranking-001', 'run-003', 'neural_v1'),
                   ('asn-004', 'exp-ranking-001', 'run-004', 'neural_v1')
        """)

        # Results
        con.execute("""
            INSERT INTO ab_results (result_id, experiment_id, run_key, variant, primary_metric, secondary_metric, duration_ms)
            VALUES ('res-001', 'exp-ranking-001', 'run-001', 'control', 0.75, 0.80, 120.0),
                   ('res-002', 'exp-ranking-001', 'run-002', 'control', 0.72, 0.78, 115.0),
                   ('res-003', 'exp-ranking-001', 'run-003', 'neural_v1', 0.88, 0.85, 200.0),
                   ('res-004', 'exp-ranking-001', 'run-004', 'neural_v1', 0.91, 0.87, 210.0)
        """)

        # Shadow runs
        con.execute("""
            INSERT INTO ab_shadow_runs (run_key, experiment_id, variant, layer, duration_ms, judge_score, tokens_used, cost_usd, error_type)
            VALUES ('shadow-001', 'exp-ranking-001', 'control', 'search_ranking', 120.0, 0.82, 500, 0.001, NULL),
                   ('shadow-002', 'exp-ranking-001', 'control', 'search_ranking', 130.0, 0.79, 520, 0.0011, NULL),
                   ('shadow-003', 'exp-ranking-001', 'neural_v1', 'search_ranking', 200.0, 0.91, 800, 0.002, NULL),
                   ('shadow-004', 'exp-ranking-001', 'neural_v1', 'search_ranking', 190.0, 0.93, 780, 0.0019, NULL)
        """)

        con.close()

        ensure_views(db_path=str(db_path))

    # ------------------------------------------------------------------
    # v_ab_experiment_summary
    # ------------------------------------------------------------------

    def test_v_ab_experiment_summary_returns_expected_row(self) -> None:
        import duckdb

        db_path = Path("test_ab_summary.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            self._setup_db(db_path)
            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT experiment_id, layer, status, variant_a, variant_b, "
                "variant_count, assignment_count, unique_run_count, "
                "avg_primary_metric, avg_secondary_metric, avg_duration_ms, result_count "
                "FROM v_ab_experiment_summary "
                "WHERE experiment_id = 'exp-ranking-001'"
            ).fetchone()
            con.close()

            assert row is not None, "Expected a row from v_ab_experiment_summary"
            assert row[0] == "exp-ranking-001"
            assert row[1] == "search_ranking"
            assert row[2] == "active"
            assert row[3] == "control"
            assert row[4] == "neural_v1"
            assert row[5] == 2  # variant_count
            assert row[6] == 4  # assignment_count
            assert row[7] == 4  # unique_run_count
            import pytest

            assert row[8] == pytest.approx(0.815, abs=1e-9)  # avg_primary_metric
            assert row[9] == pytest.approx(0.825, abs=1e-9)  # avg_secondary_metric
            assert row[10] == pytest.approx(161.25, abs=1e-9)  # avg_duration_ms
            assert row[11] == 4  # result_count
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_v_ab_experiment_summary_empty_experiment(self) -> None:
        """An experiment with no variants/assignments/results should still appear."""
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_ab_experiments,
        )
        from kindly_web_search_mcp_server.analytics.writers.schema import (
            ensure_store_schema,
        )
        from kindly_web_search_mcp_server.analytics.views import ensure_views

        db_path = Path("test_ab_summary_empty.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            ensure_store_schema(db_path=str(db_path))

            con = duckdb.connect(str(db_path))
            try:
                _ensure_ab_experiments(con)
            finally:
                con.close()
            con = duckdb.connect(str(db_path))
            con.execute("""
                INSERT INTO ab_experiments (experiment_id, layer, variant_a, variant_b, allocation_rate, status)
                VALUES ('exp-empty', 'test_layer', 'ctrl', 'test', 0.5, 'draft')
            """)
            con.close()

            ensure_views(db_path=str(db_path))

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT experiment_id, variant_count, assignment_count, "
                "unique_run_count, avg_primary_metric, result_count "
                "FROM v_ab_experiment_summary "
                "WHERE experiment_id = 'exp-empty'"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "exp-empty"
            assert row[1] == 0  # variant_count (no variants)
            assert row[2] == 0  # assignment_count
            assert row[3] == 0  # unique_run_count
            assert row[4] is None  # avg_primary_metric (no results)
            assert row[5] == 0  # result_count
        finally:
            if db_path.exists():
                db_path.unlink()

    # ------------------------------------------------------------------
    # v_ab_variant_comparison
    # ------------------------------------------------------------------

    def test_v_ab_variant_comparison_returns_both_variants(self) -> None:
        import duckdb

        db_path = Path("test_ab_compare.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            self._setup_db(db_path)
            con = duckdb.connect(str(db_path), read_only=True)
            rows = con.execute(
                "SELECT experiment_id, variant, run_count, avg_primary_metric, "
                "avg_secondary_metric, avg_duration_ms, stddev_primary_metric, "
                "result_count, variant_role "
                "FROM v_ab_variant_comparison "
                "WHERE experiment_id = 'exp-ranking-001' "
                "ORDER BY variant"
            ).fetchall()
            con.close()

            assert len(rows) == 2, f"Expected 2 variant rows, got {len(rows)}"

            # control variant
            ctrl = rows[0]
            assert ctrl[0] == "exp-ranking-001"
            assert ctrl[1] == "control"
            assert ctrl[2] == 2  # run_count
            import pytest

            assert ctrl[3] == pytest.approx(0.735, abs=1e-9)  # avg_primary_metric
            assert ctrl[4] == pytest.approx(0.79, abs=1e-9)  # avg_secondary_metric
            assert ctrl[5] == pytest.approx(117.5, abs=1e-9)  # avg_duration_ms
            assert ctrl[7] == 2  # result_count
            assert ctrl[8] == "control"

            # neural_v1 variant
            test = rows[1]
            assert test[0] == "exp-ranking-001"
            assert test[1] == "neural_v1"
            assert test[2] == 2  # run_count
            assert test[3] == pytest.approx(0.895, abs=1e-9)  # avg_primary_metric
            assert test[4] == pytest.approx(0.86, abs=1e-9)  # avg_secondary_metric
            assert test[5] == pytest.approx(205.0, abs=1e-9)  # avg_duration_ms
            assert test[7] == 2  # result_count
            assert test[8] == "treatment"
        finally:
            if db_path.exists():
                db_path.unlink()

    # ------------------------------------------------------------------
    # v_ab_shadow_run_analysis
    # ------------------------------------------------------------------

    def test_v_ab_shadow_run_analysis_returns_expected_rows(self) -> None:
        import duckdb

        db_path = Path("test_ab_shadow.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            self._setup_db(db_path)
            con = duckdb.connect(str(db_path), read_only=True)
            rows = con.execute(
                "SELECT run_key, experiment_id, variant, shadow_duration_ms, "
                "judge_score, variant_role, latency_delta_ms, judge_score_delta "
                "FROM v_ab_shadow_run_analysis "
                "WHERE experiment_id = 'exp-ranking-001' "
                "ORDER BY run_key"
            ).fetchall()
            con.close()

            assert len(rows) == 4, f"Expected 4 shadow run rows, got {len(rows)}"

            # shadow-001: control, 120ms, judge 0.82
            r0 = rows[0]
            assert r0[0] == "shadow-001"
            assert r0[1] == "exp-ranking-001"
            assert r0[2] == "control"
            assert r0[3] == 120.0
            assert r0[4] == 0.82
            assert r0[5] == "control"
            # control avg latency = (120+130)/2 = 125, delta = 120-125 = -5
            assert r0[6] == -5.0
            # control avg judge = (0.82+0.79)/2 = 0.805, delta = 0.82-0.805 = 0.015
            import pytest

            assert r0[7] == pytest.approx(0.015, abs=1e-9)

            # shadow-002: control, 130ms, judge 0.79
            r1 = rows[1]
            assert r1[0] == "shadow-002"
            assert r1[2] == "control"
            assert r1[3] == 130.0
            assert r1[4] == 0.79
            assert r1[6] == 5.0  # 130-125
            assert r1[7] == pytest.approx(-0.015, abs=1e-9)  # 0.79-0.805

            # shadow-003: neural_v1, 200ms, judge 0.91
            r2 = rows[2]
            assert r2[0] == "shadow-003"
            assert r2[2] == "neural_v1"
            assert r2[3] == 200.0
            assert r2[4] == 0.91
            assert r2[5] == "treatment"
            # neural_v1 avg latency = (200+190)/2 = 195, delta = 200-195 = 5
            assert r2[6] == 5.0
            # neural_v1 avg judge = (0.91+0.93)/2 = 0.92, delta = 0.91-0.92 = -0.01
            assert r2[7] == pytest.approx(-0.01, abs=1e-9)

            # shadow-004: neural_v1, 190ms, judge 0.93
            r3 = rows[3]
            assert r3[0] == "shadow-004"
            assert r3[2] == "neural_v1"
            assert r3[3] == 190.0
            assert r3[4] == 0.93
            assert r3[6] == -5.0  # 190-195
            assert r3[7] == pytest.approx(0.01, abs=1e-9)  # 0.93-0.92
        finally:
            if db_path.exists():
                db_path.unlink()
