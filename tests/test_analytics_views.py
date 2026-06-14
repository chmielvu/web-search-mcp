from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestAnalyticsViews:
    def _append_event(self, event_name: str, payload: dict[str, object], db_path: Path) -> None:
        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event

        append_event(event_name, payload, db_path=str(db_path))

    def _ensure_local_views(self, db_path: Path) -> None:
        from kindly_web_search_mcp_server.analytics.views import ensure_local_views

        ensure_local_views(db_path=str(db_path))

    def test_candidate_views_track_raw_search_events(self) -> None:
        import duckdb

        db_path = Path("test_views_candidate.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            result = {
                "title": "FastMCP",
                "link": "https://fastmcp.dev",
                "snippet": "FastMCP docs",
                "domain": "fastmcp.dev",
                "providers": ["searxng"],
                "provider_count": 1,
                "score": 0.95,
                "raw_score": 0.93,
            }
            self._append_event(
                "provider.search.result",
                {
                    "run_key": "run-001",
                    "provider_name": "searxng",
                    "query": "FastMCP Python SDK",
                    "duration_ms": 300.0,
                    "results": [result],
                },
                db_path,
            )
            self._append_event(
                "search.pipeline.branches",
                {
                    "run_key": "run-001",
                    "query": "FastMCP Python SDK",
                    "branches": [
                        {
                            "query": "FastMCP Python SDK",
                            "weight": 1.0,
                            "providers": ["searxng"],
                            "results": [result],
                        }
                    ],
                },
                db_path,
            )
            self._append_event(
                "search.orchestrator.response",
                {
                    "run_key": "run-001",
                    "query": "FastMCP Python SDK",
                    "merged_results": [result],
                },
                db_path,
            )
            self._append_event(
                "search.rerank.summary",
                {
                    "run_key": "run-001",
                    "query": "FastMCP Python SDK",
                    "provider": "voyage",
                    "model": "rerank-2.5",
                    "results": [result],
                },
                db_path,
            )
            self._append_event(
                "tool.web_search.response",
                {
                    "run_key": "run-001",
                    "query": "FastMCP Python SDK",
                    "results": [result],
                },
                db_path,
            )

            self._ensure_local_views(db_path)

            con = duckdb.connect(str(db_path), read_only=True)
            try:
                provider_rows = con.execute(
                    "SELECT provider, title, score, provider_count FROM vw_provider_results WHERE run_key = 'run-001'"
                ).fetchall()
                branch_rows = con.execute(
                    "SELECT branch_index, branch_query, result_index, url FROM vw_branch_candidates WHERE run_key = 'run-001'"
                ).fetchall()
                survival_rows = con.execute(
                    "SELECT stage, COUNT(*) AS rows FROM vw_candidate_survival WHERE run_key = 'run-001' GROUP BY 1 ORDER BY 1"
                ).fetchall()
                event_row = con.execute(
                    "SELECT event_name, provider, query FROM vw_events WHERE run_key = 'run-001' AND event_name = 'provider.search.result'"
                ).fetchone()
            finally:
                con.close()

            assert event_row == ("provider.search.result", "searxng", "FastMCP Python SDK")
            assert provider_rows == [("searxng", "FastMCP", 0.95, 1)]
            assert branch_rows == [(0, "FastMCP Python SDK", 0, "https://fastmcp.dev")]
            assert survival_rows == [
                ("branch", 1),
                ("final", 1),
                ("merged", 1),
                ("provider", 1),
                ("reranked", 1),
            ]
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_cache_middleware_content_error_views(self) -> None:
        import duckdb

        db_path = Path("test_views_derived.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            self._append_event(
                "search.cache.lookup",
                {
                    "run_key": "run-002",
                    "cache_type": "exact",
                    "lookup_status": "hit",
                    "hit": True,
                    "duration_ms": 12.5,
                    "similarity_score": 0.91,
                    "provider": "searxng",
                    "tool_name": "web_search",
                    "query": "duckdb",
                },
                db_path,
            )
            self._append_event(
                "middleware.rate_limit.throttled",
                {
                    "run_key": "run-002",
                    "tool_name": "perplexity_search",
                    "bucket": "expensive",
                    "waited_seconds": 2.5,
                    "attempt_count": 3,
                    "session_id": "session-1",
                    "provider": "perplexity",
                },
                db_path,
            )
            self._append_event(
                "session.started",
                {
                    "run_key": "run-002",
                    "session_id": "session-1",
                    "tool_name": "web_search",
                    "tool_count": 2,
                    "session_timeout_seconds": 60,
                    "scope": "codex",
                },
                db_path,
            )
            self._append_event(
                "content.stage.resolution",
                {
                    "run_key": "run-002",
                    "stage": "resolution",
                    "status": "success",
                    "reason": "ok",
                    "success": True,
                    "size_bytes": 120.0,
                    "duration_seconds": 0.5,
                    "word_count": 20,
                    "extraction_method": "http",
                },
                db_path,
            )
            self._append_event(
                "provider.search.error",
                {
                    "run_key": "run-002",
                    "provider_name": "searxng",
                    "query": "duckdb",
                    "duration_ms": 99.0,
                    "error_type": "TimeoutError",
                },
                db_path,
            )

            self._ensure_local_views(db_path)

            con = duckdb.connect(str(db_path), read_only=True)
            try:
                cache_row = con.execute(
                    "SELECT cache_type, lookup_status, cache_hit_text, ROUND(similarity_score, 2) FROM vw_cache_lookups WHERE run_key = 'run-002'"
                ).fetchone()
                middleware_row = con.execute(
                    "SELECT middleware_kind, tool_name, bucket, waited_seconds, attempt_count, session_id FROM vw_middleware_events WHERE run_key = 'run-002'"
                ).fetchone()
                session_row = con.execute(
                    "SELECT session_id, session_state, tool_name, tool_count FROM vw_session_activity WHERE session_id = 'session-1'"
                ).fetchone()
                content_row = con.execute(
                    "SELECT content_event_kind, stage, status, word_count FROM vw_content_events WHERE run_key = 'run-002'"
                ).fetchone()
                error_row = con.execute(
                    "SELECT event_name, provider, error_type FROM vw_error_events WHERE run_key = 'run-002'"
                ).fetchone()
            finally:
                con.close()

            assert cache_row == ("exact", "hit", "true", 0.91)
            assert middleware_row == ("rate_limit", "perplexity_search", "expensive", 2.5, 3, "session-1")
            assert session_row == ("session-1", "started", "web_search", 2)
            assert content_row == ("resolution", "resolution", "success", 20)
            assert error_row == ("provider.search.error", "searxng", "TimeoutError")
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_eval_views_join_current_tables(self) -> None:
        import duckdb

        db_path = Path("test_views_eval.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            self._append_event(
                "tool.get_content.response",
                {
                    "run_key": "eval-run-1",
                    "input_url": "https://example.com/article",
                    "normalized_url": "https://example.com/article",
                    "fetched_url": "https://example.com/article",
                    "status": "success",
                    "source_type": "html",
                    "fetch_backend": "safe_http_extract",
                    "content_type": "text/markdown",
                    "page_content": "alpha beta gamma",
                    "word_count": 3,
                    "window": {
                        "offset": 0,
                        "length": 20,
                        "returned_chars": 16,
                        "total_chars": 16,
                        "has_more": False,
                        "next_offset": None,
                    },
                    "metadata": {"title": "Example"},
                    "links": [],
                    "summary": None,
                },
                db_path,
            )

            self._ensure_local_views(db_path)

            con = duckdb.connect(str(db_path))
            con.execute(
                """
                INSERT INTO eval_runs VALUES (
                    'eval-run-1',
                    CURRENT_TIMESTAMP,
                    'suite-a',
                    'tester',
                    'dataset-a',
                    'prompt-v1',
                    '{"notes":"ok"}',
                    '{"suite":"suite-a"}'
                )
                """
            )
            con.execute(
                """
                INSERT INTO eval_cases VALUES (
                    'case-1',
                    'eval-run-1',
                    CURRENT_TIMESTAMP,
                    'web_search',
                    'fetch quality',
                    'good fetch',
                    '{"expected":"markdown"}',
                    '{"label":"smoke"}',
                    'trace-1',
                    'eval-run-1',
                    '{"case":"case-1"}'
                )
                """
            )
            con.execute(
                """
                INSERT INTO eval_observations VALUES (
                    'obs-1',
                    'eval-run-1',
                    'case-1',
                    CURRENT_TIMESTAMP,
                    'tool.web_search.response',
                    'eval-run-1',
                    0.8,
                    'pass',
                    '{"note":"ok"}',
                    '{"obs":"obs-1"}'
                )
                """
            )
            con.execute(
                """
                INSERT INTO llm_quality_scores VALUES (
                    'score-1',
                    'eval-run-1',
                    'case-1',
                    CURRENT_TIMESTAMP,
                    'clarity',
                    0.9,
                    'gpt-4.1',
                    'good answer',
                    '{"score":"score-1"}'
                )
                """
            )
            con.close()

            self._ensure_local_views(db_path)

            con = duckdb.connect(str(db_path), read_only=True)
            try:
                provider_quality = con.execute(
                    "SELECT suite_name, target_tool, cases, passes, fails, avg_score FROM vw_eval_provider_quality WHERE suite_name = 'suite-a'"
                ).fetchone()
                fetch_quality = con.execute(
                    "SELECT eval_run_id, eval_case_id, fetch_backend, status, fetch_events FROM vw_eval_fetch_quality WHERE eval_run_id = 'eval-run-1'"
                ).fetchone()
            finally:
                con.close()

            assert provider_quality == ("suite-a", "web_search", 1, 1, 0, 0.8)
            assert fetch_quality == ("eval-run-1", "case-1", "safe_http_extract", "success", 1)
        finally:
            if db_path.exists():
                db_path.unlink()
