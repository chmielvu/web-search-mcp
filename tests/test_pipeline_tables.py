from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestProviderCallsSchema:
    """Test provider_calls table."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_provider_calls,
            insert_provider_calls,
        )

        db_path = Path("test_provider_calls.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_provider_calls(con)
            con.close()

            insert_provider_calls(
                run_key="run-004",
                provider="searxng",
                branch_index=0,
                branch_query="FastMCP Python SDK",
                num_results_requested=10,
                num_results_returned=8,
                duration_ms=345.6,
                error_code=None,
                error_message=None,
                http_status=200,
                payload_json=json.dumps({"engine": "google"}),
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT run_key, provider, branch_index, num_results_returned, http_status, payload_json FROM provider_calls"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-004"
            assert row[1] == "searxng"
            assert row[2] == 0
            assert row[3] == 8
            assert row[4] == 200
            assert json.loads(row[5]) == {"engine": "google"}
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_indexes_exist(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import _ensure_provider_calls

        db_path = Path("test_provider_calls_idx.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_provider_calls(con)
            indexes = {r[0] for r in con.execute(
                "SELECT index_name FROM duckdb_indexes WHERE table_name = 'provider_calls'"
            ).fetchall()}
            con.close()
            assert "idx_pc_run_key" in indexes
            assert "idx_pc_provider" in indexes
        finally:
            if db_path.exists():
                db_path.unlink()


class TestProviderCandidatesSchema:
    """Test provider_candidates table."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_provider_candidates,
            insert_provider_candidates,
        )

        db_path = Path("test_provider_candidates.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_provider_candidates(con)
            con.close()

            insert_provider_candidates(
                run_key="run-004",
                provider="searxng",
                branch_index=0,
                rank=1,
                title="FastMCP",
                link="https://fastmcp.dev",
                snippet="FastMCP Python SDK",
                domain="fastmcp.dev",
                score=0.95,
                published_date="2025-06-01",
                payload_json=None,
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT run_key, provider, rank, title, link, score FROM provider_candidates"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-004"
            assert row[1] == "searxng"
            assert row[2] == 1
            assert row[3] == "FastMCP"
            assert row[4] == "https://fastmcp.dev"
            assert row[5] == 0.95
        finally:
            if db_path.exists():
                db_path.unlink()


class TestMergedCandidatesSchema:
    """Test merged_candidates table."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_merged_candidates,
            insert_merged_candidates,
        )

        db_path = Path("test_merged_candidates.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_merged_candidates(con)
            con.close()

            insert_merged_candidates(
                run_key="run-005",
                rank=1,
                title="FastMCP",
                link="https://fastmcp.dev",
                snippet="SDK",
                domain="fastmcp.dev",
                rrf_score=32.5,
                provider_count=2,
                providers=["searxng", "brave"],
                overlap_flag=True,
                payload_json=None,
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT run_key, rank, rrf_score, provider_count, overlap_flag FROM merged_candidates"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-005"
            assert row[1] == 1
            assert row[2] == 32.5
            assert row[3] == 2
            assert row[4] is True
        finally:
            if db_path.exists():
                db_path.unlink()


class TestRerankStagesSchema:
    """Test rerank_stages table."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_rerank_stages,
            insert_rerank_stages,
        )

        db_path = Path("test_rerank_stages.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_rerank_stages(con)
            con.close()

            insert_rerank_stages(
                run_key="run-006",
                stage="cross_encoder",
                provider="local",
                model="ms-marco-MiniLM-L-6-v2",
                input_count=10,
                output_count=8,
                duration_ms=234.5,
                max_score=0.98,
                avg_score=0.76,
                score_threshold=0.5,
                instruction_present=True,
                instruction_length=120,
                query_type_hint="research",
                entity_overlap_enabled=False,
                payload_json=None,
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT run_key, stage, model, input_count, max_score FROM rerank_stages"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-006"
            assert row[1] == "cross_encoder"
            assert row[2] == "ms-marco-MiniLM-L-6-v2"
            assert row[3] == 10
            assert row[4] == 0.98
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_indexes_exist(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import _ensure_rerank_stages

        db_path = Path("test_rs_idx.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_rerank_stages(con)
            indexes = {r[0] for r in con.execute(
                "SELECT index_name FROM duckdb_indexes WHERE table_name = 'rerank_stages'"
            ).fetchall()}
            con.close()
            assert "idx_rs_run_key" in indexes
            assert "idx_rs_stage" in indexes
        finally:
            if db_path.exists():
                db_path.unlink()


class TestRerankCandidatesSchema:
    """Test rerank_candidates table."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_rerank_candidates,
            insert_rerank_candidates,
        )

        db_path = Path("test_rerank_candidates.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_rerank_candidates(con)
            con.close()

            insert_rerank_candidates(
                run_key="run-006",
                stage="cross_encoder",
                link="https://fastmcp.dev",
                rank_before=3,
                rank_after=1,
                score_before=0.65,
                score_after=0.92,
                score_after_relevance=0.88,
                score_after_recency=0.02,
                score_after_entity=0.02,
                recency_boost=0.02,
                entity_overlap_score=0.02,
                diversity_removed=False,
                payload_json=None,
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT run_key, stage, link, rank_before, rank_after, score_after FROM rerank_candidates"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-006"
            assert row[1] == "cross_encoder"
            assert row[2] == "https://fastmcp.dev"
            assert row[3] == 3
            assert row[4] == 1
            assert row[5] == 0.92
        finally:
            if db_path.exists():
                db_path.unlink()


class TestFinalResultsSchema:
    """Test final_results table."""

    def test_create_and_insert_round_trip(self) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_final_results,
            insert_final_results,
        )

        db_path = Path("test_final_results.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            con = duckdb.connect(str(db_path))
            _ensure_final_results(con)
            con.close()

            insert_final_results(
                run_key="run-007",
                rank=1,
                title="FastMCP",
                link="https://fastmcp.dev",
                snippet="Python SDK",
                domain="fastmcp.dev",
                final_score=0.92,
                providers=["searxng", "brave"],
                provider_count=2,
                entities_count=3,
                payload_json=None,
                db_path=str(db_path),
            )

            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT run_key, rank, final_score, provider_count, entities_count FROM final_results"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-007"
            assert row[1] == 1
            assert row[2] == 0.92
            assert row[3] == 2
            assert row[4] == 3
        finally:
            if db_path.exists():
                db_path.unlink()
