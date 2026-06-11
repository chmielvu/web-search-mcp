from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestSearchQualityScores:
    """Test search_quality_scores table."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_search_quality_scores,
            insert_search_quality_scores,
        )

        db_path = Path("test_sqs.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_search_quality_scores(con)
            con.close()

            insert_search_quality_scores(
                run_key="run-sqs-001",
                provider_overlap_rate=0.33,
                domain_diversity_count=2,
                domain_diversity_ratio=0.67,
                rerank_compression_ratio=0.8,
                avg_rrf_score=30.25,
                top_score=32.5,
                p95_score=28.0,
                rewrite_variant_count=1,
                provider_count=2,
                branch_count=1,
                total_candidates_input=15,
                total_candidates_merged=10,
                total_candidates_reranked=8,
                total_final_results=5,
                payload_json=None,
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT run_key, provider_overlap_rate, domain_diversity_count, top_score, total_final_results FROM search_quality_scores"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-sqs-001"
            assert row[1] == 0.33
            assert row[2] == 2
            assert row[3] == 32.5
            assert row[4] == 5
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_primary_key_prevents_duplicate(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_search_quality_scores,
            insert_search_quality_scores,
        )

        db_path = Path("test_sqs_pk.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_search_quality_scores(con)
            con.close()

            insert_search_quality_scores(
                run_key="run-sqs-dup",
                provider_overlap_rate=0.1,
                db_path=str(db_path),
            )
            insert_search_quality_scores(
                run_key="run-sqs-dup",
                provider_overlap_rate=0.2,
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT provider_overlap_rate FROM search_quality_scores WHERE run_key = 'run-sqs-dup'"
            ).fetchone()
            con.close()

            # ON CONFLICT DO NOTHING means first insert wins
            assert row is not None
            assert row[0] == 0.1
        finally:
            if db_path.exists():
                db_path.unlink()


class TestSummaryTables:
    """Test summary tables creation."""

    def test_summary_tables_exist(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_summary_provider_daily,
            _ensure_summary_intent_daily,
            _ensure_summary_rerank_daily,
            _ensure_summary_quality_daily,
        )

        db_path = Path("test_summary.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_summary_provider_daily(con)
            _ensure_summary_intent_daily(con)
            _ensure_summary_rerank_daily(con)
            _ensure_summary_quality_daily(con)

            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            con.close()

            assert "summary_provider_daily" in tables
            assert "summary_intent_daily" in tables
            assert "summary_rerank_daily" in tables
            assert "summary_quality_daily" in tables
        finally:
            if db_path.exists():
                db_path.unlink()
