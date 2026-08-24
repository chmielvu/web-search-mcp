from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestComputeSearchQuality:
    """Test the compute_search_quality function end-to-end."""

    def test_compute_inserts_correct_values(self) -> None:
        import duckdb

        from kindly_web_search_mcp_server.analytics.quality_metrics import (
            compute_search_quality,
        )
        from kindly_web_search_mcp_server.analytics.writers.core import (
            insert_final_results,
            insert_provider_calls,
            insert_rerank_candidates,
            insert_rerank_stages,
            insert_search_branches,
            insert_search_candidates,
            insert_search_run,
        )
        from kindly_web_search_mcp_server.analytics.writers.schema import (
            ensure_store_schema,
        )

        db_path = Path("test_quality_metrics.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            # ── 1. Create all prerequisite tables ──────────────────────
            ensure_store_schema(db_path=str(db_path))

            run_key = "run-qm-001"

            # ── 2. Insert sample data ──────────────────────────────────
            # search_runs (needed by the pipeline but not directly queried)
            insert_search_run(
                run_key=run_key,
                query="test query",
                db_path=str(db_path),
            )

            # provider_calls – 2 distinct providers, 8 + 7 results
            insert_provider_calls(
                run_key=run_key,
                provider="searxng",
                branch_index=0,
                branch_query="test query",
                num_results_requested=10,
                num_results_returned=8,
                duration_ms=100.0,
                db_path=str(db_path),
            )
            insert_provider_calls(
                run_key=run_key,
                provider="brave",
                branch_index=0,
                branch_query="test query",
                num_results_requested=10,
                num_results_returned=7,
                duration_ms=150.0,
                db_path=str(db_path),
            )

            # search_branches — 2 rows to drive `branch_count == 2`
            # (compute_search_quality reads from search_branches).
            # Replaces the deleted provider_candidates insert path.
            insert_search_branches(
                run_key=run_key,
                branch_index=0,
                branch_role="original",
                branch_query="test query",
                branch_why="primary branch",
                support_terms=[],
                max_results=10,
                assigned_providers=["searxng", "brave"],
                attempted_providers=["searxng", "brave"],
                skipped_providers=[],
                results_count=4,
                latency_ms=100.0,
                db_path=str(db_path),
            )
            insert_search_branches(
                run_key=run_key,
                branch_index=1,
                branch_role="semantic_tavily",
                branch_query="test query rephrased",
                branch_why="secondary branch",
                support_terms=[],
                max_results=5,
                assigned_providers=["tavily"],
                attempted_providers=["tavily"],
                skipped_providers=[],
                results_count=0,
                latency_ms=50.0,
                db_path=str(db_path),
            )

            # search_candidates – 4 rows, 2 with overlap_flag=true
            # overlap_rate = 2/4 = 0.5
            # avg_rrf_score = (32.5 + 28.0 + 30.0 + 25.0) / 4 = 28.875
            for i, (rrf, ov) in enumerate(
                [(32.5, True), (28.0, False), (30.0, True), (25.0, False)],
                start=1,
            ):
                insert_search_candidates(
                    run_key=run_key,
                    title=f"Result {i}",
                    link=f"https://example.com/{i}",
                    snippet="snip",
                    domain="example.com",
                    rrf_score=rrf,
                    provider_count=1,
                    providers=["searxng"],
                    overlap_flag=ov,
                    db_path=str(db_path),
                )
            # rerank_stages – input_count=10, output_count=6
            # compression_ratio = 10/6 ≈ 1.6667
            insert_rerank_stages(
                run_key=run_key,
                stage="cross_encoder",
                provider="local",
                model="test-model",
                input_count=10,
                output_count=6,
                duration_ms=50.0,
                max_score=0.98,
                avg_score=0.75,
                db_path=str(db_path),
            )

            # rerank_candidates – 3 rows with score_after values
            # top_score = MAX(0.92, 0.85, 0.75) = 0.92
            # p95_score approx_quantile(0.95) — with 3 values the
            # approximation should be close to 0.92 (the max)
            scores = [0.75, 0.85, 0.92]
            for i, score in enumerate(scores):
                insert_rerank_candidates(
                    run_key=run_key,
                    stage="cross_encoder",
                    link=f"https://example.com/rerank-{i}",
                    rank_before=i + 1,
                    rank_after=i + 1,
                    score_before=score - 0.1,
                    score_after=score,
                    db_path=str(db_path),
                )

            # final_results – 3 rows, 2 distinct domains
            # domain_diversity_count = 2
            # domain_diversity_ratio = 2/3 ≈ 0.6667
            insert_final_results(
                run_key=run_key,
                rank=1,
                domain="example.com",
                link="https://example.com/1",
                title="Title 1",
                snippet="snip1",
                final_score=0.92,
                providers=["searxng"],
                provider_count=1,
                db_path=str(db_path),
            )
            insert_final_results(
                run_key=run_key,
                rank=2,
                domain="example.org",
                link="https://example.org/2",
                title="Title 2",
                snippet="snip2",
                final_score=0.85,
                providers=["brave"],
                provider_count=1,
                db_path=str(db_path),
            )
            insert_final_results(
                run_key=run_key,
                rank=3,
                domain="example.com",
                link="https://example.com/3",
                title="Title 3",
                snippet="snip3",
                final_score=0.75,
                providers=["searxng"],
                provider_count=1,
                db_path=str(db_path),
            )

            # query_rewrites table was removed in the 2026-07-20 schema
            # consolidation; rewrite_variant_count is computed from
            # surviving tables.

            # ── 3. Call compute_search_quality ─────────────────────────
            metrics = compute_search_quality(run_key, db_path=str(db_path))

            # ── 4. Verify returned metrics dict ────────────────────────
            assert metrics["provider_overlap_rate"] == 0.5
            assert metrics["domain_diversity_count"] == 2
            assert metrics["domain_diversity_ratio"] == 2.0 / 3.0  # 0.6667
            assert metrics["rerank_compression_ratio"] == 10.0 / 6.0  # 1.6667
            assert metrics["avg_rrf_score"] == 28.875
            assert metrics["top_score"] == 0.92
            assert metrics["p95_score"] is not None  # approx quantile ok
            assert metrics["provider_count"] == 2
            assert metrics["branch_count"] == 2
            assert metrics["total_candidates_input"] == 15  # 8 + 7
            assert metrics["total_candidates_merged"] == 4
            assert metrics["total_candidates_reranked"] == 6
            assert metrics["total_final_results"] == 3

            # ── 5. Verify the row was persisted in search_quality_scores ─
            con2 = duckdb.connect(str(db_path), read_only=True)
            row = con2.execute(
                "SELECT run_key, provider_overlap_rate, domain_diversity_count, "
                "       domain_diversity_ratio, rerank_compression_ratio, "
                "       avg_rrf_score, top_score, p95_score, "
                "       provider_count, branch_count, "
                "       total_candidates_input, total_candidates_merged, "
                "       total_candidates_reranked, total_final_results "
                "FROM search_quality_scores WHERE run_key = ?",
                [run_key],
            ).fetchone()
            con2.close()

            assert row is not None, "Expected a row in search_quality_scores"
            assert row[0] == run_key
            assert row[1] == 0.5  # provider_overlap_rate
            assert row[2] == 2  # domain_diversity_count
            assert row[3] == 2.0 / 3.0  # domain_diversity_ratio
            assert row[4] == 10.0 / 6.0  # rerank_compression_ratio
            assert row[5] == 28.875  # avg_rrf_score
            assert row[6] == 0.92  # top_score
            assert row[7] is not None  # p95_score (approx, just check exists)
            assert row[8] == 2  # provider_count
            assert row[9] == 2  # branch_count
            assert row[10] == 15  # total_candidates_input
            assert row[11] == 4  # total_candidates_merged
            assert row[12] == 6  # total_candidates_reranked
            assert row[13] == 3  # total_final_results

        finally:
            if db_path.exists():
                db_path.unlink()

    def test_empty_run_returns_none_metrics(self) -> None:
        """A run_key with no data across all tables returns None for each
        metric and does NOT crash."""
        import duckdb
        from kindly_web_search_mcp_server.analytics.quality_metrics import (
            compute_search_quality,
        )
        from kindly_web_search_mcp_server.analytics.writers.core import (
            insert_search_run,
        )
        from kindly_web_search_mcp_server.analytics.writers.schema import (
            ensure_store_schema,
        )

        db_path = Path("test_quality_empty.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            ensure_store_schema(db_path=str(db_path))

            run_key = "run-qm-empty"
            insert_search_run(run_key=run_key, query="empty run", db_path=str(db_path))

            metrics = compute_search_quality(run_key, db_path=str(db_path))

            assert metrics["provider_overlap_rate"] is None
            # COUNT(*) / COUNT(DISTINCT) over empty tables returns 0 (not None)
            assert metrics["domain_diversity_count"] == 0
            assert metrics["domain_diversity_ratio"] is None
            assert metrics["rerank_compression_ratio"] is None
            assert metrics["avg_rrf_score"] is None
            assert metrics["top_score"] is None
            assert metrics["p95_score"] is None
            assert metrics["provider_count"] == 0
            assert metrics["total_candidates_input"] is None
            assert metrics["total_candidates_merged"] == 0
            assert metrics["total_candidates_reranked"] is None
            assert metrics["total_final_results"] == 0

            # Also verify it was persisted
            con2 = duckdb.connect(str(db_path), read_only=True)
            row = con2.execute(
                "SELECT run_key FROM search_quality_scores WHERE run_key = ?",
                [run_key],
            ).fetchone()
            con2.close()
            assert row is not None

        finally:
            if db_path.exists():
                db_path.unlink()
