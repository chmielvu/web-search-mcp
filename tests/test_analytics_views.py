from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestAnalyticsViews:
    """Test analytics views with sample data."""

    def _setup_db(self, db_path: Path) -> None:
        import duckdb
        from kindly_web_search_mcp_server.analytics.duckdb_store import (
            _ensure_search_runs,
            _ensure_query_understanding,
            _ensure_query_rewrites,
            _ensure_provider_calls,
            _ensure_provider_candidates,
            _ensure_merged_candidates,
            _ensure_rerank_stages,
            _ensure_rerank_candidates,
            _ensure_final_results,
            _ensure_search_quality_scores,
            _ensure_judge_evaluations,
        )
        from kindly_web_search_mcp_server.analytics.views import ensure_views

        con = duckdb.connect(str(db_path))
        _ensure_search_runs(con)
        _ensure_query_understanding(con)
        _ensure_query_rewrites(con)
        _ensure_provider_calls(con)
        _ensure_provider_candidates(con)
        _ensure_merged_candidates(con)
        _ensure_rerank_stages(con)
        _ensure_rerank_candidates(con)
        _ensure_final_results(con)
        _ensure_search_quality_scores(con)
        _ensure_judge_evaluations(con)
        con.close()

        # Insert sample data
        con = duckdb.connect(str(db_path))
        con.execute("""
            INSERT INTO search_runs (run_key, query, normalized_query, research_goal, status, duration_ms, final_result_count, candidate_count, rewrite_enabled, tool_name)
            VALUES ('run-story-001', 'FastMCP Python SDK', 'fastmcp python sdk', 'understand API', 'success', 1200.0, 5, 15, true, 'web_search')
        """)
        con.execute("""
            INSERT INTO query_understanding (run_key, intent, confidence, should_decompose, rationale, model, provider)
            VALUES ('run-story-001', 'research', 0.92, true, 'complex query', 'gpt-4o', 'openai')
        """)
        con.execute("""
            INSERT INTO query_rewrites (run_key, variant_index, branch_type, kind, target, query, weight, reason, max_results, model)
            VALUES ('run-story-001', 0, 'core', 'academic', 'arxiv', 'FastMCP Python SDK', 1.0, 'primary', 10, 'cerebras')
        """)
        con.execute("""
            INSERT INTO provider_calls (run_key, provider, branch_index, branch_query, num_results_requested, num_results_returned, duration_ms, http_status)
            VALUES ('run-story-001', 'searxng', 0, 'FastMCP Python SDK', 10, 8, 300.0, 200),
                   ('run-story-001', 'brave', 0, 'FastMCP Python SDK', 10, 7, 250.0, 200)
        """)
        con.execute("""
            INSERT INTO provider_candidates (run_key, provider, branch_index, rank, title, link, domain, score)
            VALUES ('run-story-001', 'searxng', 0, 1, 'FastMCP', 'https://fastmcp.dev', 'fastmcp.dev', 0.95),
                   ('run-story-001', 'brave', 0, 1, 'FastMCP', 'https://fastmcp.dev', 'fastmcp.dev', 0.90),
                   ('run-story-001', 'searxng', 0, 2, 'GitHub', 'https://github.com/jlowin/fastmcp', 'github.com', 0.85)
        """)
        con.execute("""
            INSERT INTO merged_candidates (run_key, rank, title, link, domain, rrf_score, provider_count, providers, overlap_flag)
            VALUES ('run-story-001', 1, 'FastMCP', 'https://fastmcp.dev', 'fastmcp.dev', 32.5, 2, ['searxng', 'brave'], true),
                   ('run-story-001', 2, 'GitHub', 'https://github.com/jlowin/fastmcp', 'github.com', 28.0, 1, ['searxng'], false)
        """)
        con.execute("""
            INSERT INTO rerank_stages (run_key, stage, provider, model, input_count, output_count, duration_ms, max_score, avg_score)
            VALUES ('run-story-001', 'cross_encoder', 'local', 'ms-marco', 10, 8, 150.0, 0.98, 0.76)
        """)
        con.execute("""
            INSERT INTO final_results (run_key, rank, title, link, domain, final_score, providers, provider_count, entities_count)
            VALUES ('run-story-001', 1, 'FastMCP', 'https://fastmcp.dev', 'fastmcp.dev', 0.92, ['searxng', 'brave'], 2, 3),
                   ('run-story-001', 2, 'GitHub', 'https://github.com/jlowin/fastmcp', 'github.com', 0.85, ['searxng'], 1, 2)
        """)
        con.execute("""
            INSERT INTO search_quality_scores (run_key, provider_overlap_rate, domain_diversity_count, domain_diversity_ratio, rerank_compression_ratio, avg_rrf_score, top_score, p95_score, rewrite_variant_count, provider_count, branch_count, total_candidates_input, total_candidates_merged, total_candidates_reranked, total_final_results)
            VALUES ('run-story-001', 0.33, 2, 0.67, 0.8, 30.25, 32.5, 28.0, 1, 2, 1, 15, 10, 8, 5)
        """)
        con.execute("""
            INSERT INTO judge_evaluations (run_key, tool_name, judge_model, relevance_score, accuracy_score, completeness_score, source_quality_score, overall_score, rationale, duration_ms)
            VALUES ('run-story-001', 'web_search', 'gpt-4o-mini', 0.88, 0.92, 0.85, 0.90, 0.89, 'Good results', 500.0)
        """)
        con.close()

        ensure_views(db_path=str(db_path))

    def test_v_search_run_story(self) -> None:
        import duckdb

        db_path = Path("test_views_story.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            self._setup_db(db_path)
            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT run_key, query, rewrite_variant_count, provider_call_count, provider_candidate_count, merged_candidate_count, final_result_count, intent, confidence, overlap_rate, domain_diversity FROM v_search_run_story WHERE run_key = 'run-story-001'"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[0] == "run-story-001"
            assert row[1] == "FastMCP Python SDK"
            assert row[2] == 1   # rewrite_variant_count
            assert row[3] == 2   # provider_call_count
            assert row[4] == 3   # provider_candidate_count
            assert row[5] == 2   # merged_candidate_count
            assert row[6] == 2   # final_result_count (from final_results table, not search_runs)
            assert row[7] == "research"
            assert row[8] == 0.92
            assert row[9] == 0.33
            assert row[10] == 2
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_v_provider_survival_funnel(self) -> None:
        import duckdb

        db_path = Path("test_views_funnel.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            self._setup_db(db_path)
            con = duckdb.connect(str(db_path), read_only=True)
            rows = con.execute(
                "SELECT provider, runs_with_provider, provider_candidates, merged_candidates, final_results, survival_rate_pct FROM v_provider_survival_funnel ORDER BY provider"
            ).fetchall()
            con.close()

            assert len(rows) == 2
            # searxng: 2 candidates (fastmcp, github), 2 merged (fastmcp, github), 2 final (fastmcp, github)
            searxng = [r for r in rows if r[0] == "searxng"][0]
            assert searxng[1] == 1   # runs_with_provider
            assert searxng[2] == 2   # provider_candidates
            assert searxng[3] == 2   # merged_candidates
            assert searxng[4] == 2   # final_results (both fastmcp and github)
            assert searxng[5] == 200.0  # 2/1 = 200%

            # brave: 1 candidate (fastmcp), 1 merged, 1 final (fastmcp)
            brave = [r for r in rows if r[0] == "brave"][0]
            assert brave[1] == 1
            assert brave[2] == 1
            assert brave[3] == 1
            assert brave[4] == 1
            assert brave[5] == 100.0
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_v_daily_quality_summary(self) -> None:
        import duckdb

        db_path = Path("test_views_daily.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            self._setup_db(db_path)
            con = duckdb.connect(str(db_path), read_only=True)
            row = con.execute(
                "SELECT day, query_count, avg_overlap_rate, avg_domain_diversity, avg_compression_ratio, avg_top_score, avg_judge_score FROM v_daily_quality_summary"
            ).fetchone()
            con.close()

            assert row is not None
            assert row[1] == 1
            assert row[2] == 0.33
            assert row[3] == 2
            assert row[4] == 0.8
            assert row[5] == 32.5
            assert row[6] == 0.89
        finally:
            if db_path.exists():
                db_path.unlink()
