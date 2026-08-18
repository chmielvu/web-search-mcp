"""Comprehensive test suite for DuckDB analytics schema expansion and analytical views."""

from __future__ import annotations

import logging
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from kindly_web_search_mcp_server.settings import settings
from kindly_web_search_mcp_server.analytics.async_writes import drain_duckdb_writes
from kindly_web_search_mcp_server.analytics.duckdb_store import (
    ensure_store_schema,
    insert_code_search_batches,
    insert_content_operation_batches,
    insert_gemini_search_batches,
    insert_quick_web_search_batches,
)
from kindly_web_search_mcp_server.analytics.queries import build_analytics_query_plan
from kindly_web_search_mcp_server.analytics.reports import available_reports, run_report
from kindly_web_search_mcp_server.analytics.views import ensure_views
from kindly_web_search_mcp_server.utils.observability import emit_tool_observability_event


class TestDuckDBSchemaExpansion(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(f"test_expansion_{self._testMethodName}.duckdb")
        self.db_path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_schema_creates_all_17_new_fact_tables(self) -> None:
        ensure_store_schema(db_path=str(self.db_path))

        con = duckdb.connect(str(self.db_path), read_only=True)
        tables = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        con.close()

        expected_new_tables = {
            "quick_web_search_runs",
            "quick_web_search_citations",
            "gemini_search_runs",
            "gemini_search_sources",
            "gemini_search_attempts",
            "code_search_runs",
            "code_search_providers",
            "code_search_diagnostics",
            "code_search_hits",
            "code_search_hit_variants",
            "code_search_query_variants",
            "code_search_repositories",
            "code_search_rerank",
            "content_operations",
            "content_fetches",
            "content_summaries",
            "content_summary_attempts",
        }

        for table in expected_new_tables:
            self.assertIn(table, tables, f"Expected table {table} was not created by ensure_store_schema")

    def test_quick_web_search_persistence_and_views(self) -> None:
        ensure_store_schema(db_path=str(self.db_path))

        run_row = {
            "terminal_event_id": "qws-term-1",
            "tool_call_id": "qws-tool-1",
            "trace_id": "trace-123",
            "session_id": "sess-456",
            "search_id": "search-789",
            "provider_session_id": "psess-101",
            "search_queries": ["python async duckdb", "fastmcp analytics"],
            "objective": "Research duckdb async",
            "max_results": 10,
            "max_chars_total": 50000,
            "max_chars_per_result": 5000,
            "client_model": "gemini-2.5-flash",
            "include_domains": ["duckdb.org"],
            "exclude_domains": ["spam.com"],
            "after_date": "2026-01-01",
            "location": "US",
            "max_age_seconds": 3600,
            "timeout_seconds": 15.0,
            "disable_cache_fallback": False,
            "status": "success",
            "duration_ms": 320.5,
            "total_citations": 2,
            "warnings": [],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "error_type": None,
            "error_message": None,
            "payload_json": {"query": "python async duckdb"},
        }
        citation_rows = [
            {
                "terminal_event_id": "qws-term-1",
                "tool_call_id": "qws-tool-1",
                "citation_index": 0,
                "title": "DuckDB Python Guide",
                "url": "https://duckdb.org/docs/api/python/overview",
                "snippet": "DuckDB supports fast in-process analytics in Python.",
                "publish_date": "2026-01-15",
                "excerpts": ["in-process analytics"],
                "payload_json": {"title": "DuckDB Python Guide"},
            },
            {
                "terminal_event_id": "qws-term-1",
                "tool_call_id": "qws-tool-1",
                "citation_index": 1,
                "title": "FastMCP Server",
                "url": "https://github.com/prefecthq/fastmcp",
                "snippet": "FastMCP server framework for Python.",
                "publish_date": None,
                "excerpts": ["FastMCP framework"],
                "payload_json": {"title": "FastMCP Server"},
            },
        ]

        insert_quick_web_search_batches(
            quick_web_search_runs=[run_row],
            quick_web_search_citations=citation_rows,
            db_path=str(self.db_path),
        )
        drain_duckdb_writes()

        ensure_views(db_path=str(self.db_path))

        con = duckdb.connect(str(self.db_path), read_only=True)
        perf_row = con.execute(
            "SELECT client_model, status, total_runs, avg_citations, avg_duration_ms FROM vw_quick_web_search_performance"
        ).fetchone()
        self.assertIsNotNone(perf_row)
        assert perf_row is not None
        self.assertEqual(perf_row[0], "gemini-2.5-flash")
        self.assertEqual(perf_row[1], "success")
        self.assertEqual(perf_row[2], 1)
        self.assertEqual(perf_row[3], 2.0)
        sources = con.execute(
            "SELECT domain, citation_count, citations_with_date, date_presence_pct FROM vw_quick_web_search_citation_sources ORDER BY domain"
        ).fetchall()
        self.assertEqual(len(sources), 2)
        con.close()

    def test_gemini_search_persistence_and_views(self) -> None:
        ensure_store_schema(db_path=str(self.db_path))

        run_row = {
            "terminal_event_id": "gsr-term-1",
            "tool_call_id": "gsr-tool-1",
            "trace_id": "trace-gemini",
            "session_id": "sess-gemini",
            "query": "DuckDB analytics features 2026",
            "research_goal": "Understand duckdb features",
            "structured_output_requested": True,
            "mode": "structured",
            "answer": "DuckDB 2026 adds full OLAP streaming and vss integration.",
            "structured_data": {"features": ["OLAP", "VSS"]},
            "search_queries": ["duckdb features 2026"],
            "model_used": "gemini-3.5-flash-lite",
            "prompt_tokens": 500,
            "completion_tokens": 120,
            "total_tokens": 620,
            "grounding_chunks_count": 4,
            "web_search_queries_count": 2,
            "fallback_chain": ["gemini-3.5-flash-lite"],
            "fallback_reason": None,
            "status": "success",
            "duration_ms": 1150.0,
            "error_message": None,
            "payload_json": {"query": "DuckDB analytics features 2026"},
        }
        source_rows = [
            {
                "terminal_event_id": "gsr-term-1",
                "tool_call_id": "gsr-tool-1",
                "source_kind": "grounding_source",
                "source_index": 0,
                "url": "https://duckdb.org/news",
                "title": "DuckDB News",
                "source_json": {"title": "DuckDB News"},
            },
            {
                "terminal_event_id": "gsr-term-1",
                "tool_call_id": "gsr-tool-1",
                "source_kind": "url_citation",
                "source_index": 0,
                "url": "https://duckdb.org/docs",
                "title": "DuckDB Docs",
                "source_json": {"title": "DuckDB Docs"},
            },
        ]

        insert_gemini_search_batches(
            gemini_search_runs=[run_row],
            gemini_search_sources=source_rows,
            db_path=str(self.db_path),
        )
        drain_duckdb_writes()

        ensure_views(db_path=str(self.db_path))

        con = duckdb.connect(str(self.db_path), read_only=True)
        perf_row = con.execute(
            "SELECT model_used, mode, total_runs, avg_grounding_chunks, avg_total_tokens FROM vw_gemini_search_performance"
        ).fetchone()
        self.assertIsNotNone(perf_row)
        assert perf_row is not None
        self.assertEqual(perf_row[0], "gemini-3.5-flash-lite")
        self.assertEqual(perf_row[1], "structured")
        self.assertEqual(perf_row[2], 1)
        self.assertEqual(perf_row[3], 4.0)
        self.assertEqual(perf_row[4], 620.0)
        fallbacks = con.execute("SELECT terminal_event_id, final_model_used, fallback_occurred FROM vw_gemini_search_fallbacks").fetchall()
        self.assertEqual(len(fallbacks), 1)

        sources = con.execute("SELECT source_kind, domain, total_sources FROM vw_gemini_search_sources ORDER BY source_kind").fetchall()
        self.assertEqual(len(sources), 2)
        con.close()

    def test_code_search_persistence_and_views(self) -> None:
        ensure_store_schema(db_path=str(self.db_path))

        run_row = {
            "terminal_event_id": "csr-term-1",
            "tool_call_id": "csr-tool-1",
            "trace_id": "trace-code",
            "session_id": "sess-code",
            "query": "FastMCP.tool registration",
            "research_goal": "Find tool registration in FastMCP",
            "language": "python",
            "path": "src/",
            "filename": "server.py",
            "extension": "py",
            "regexp_requested": False,
            "deep_requested": True,
            "max_results_requested": 50,
            "repo_name": "prefecthq/fastmcp",
            "library_name": "fastmcp",
            "topic": "mcp",
            "repository_filters": ["prefecthq/fastmcp"],
            "planner_original_query": "FastMCP.tool registration",
            "planner_search_text": "FastMCP tool registration",
            "planner_api_query": "FastMCP.tool",
            "planner_mode": "code",
            "planner_structural_kind": "function_call",
            "planner_exa_semantic_query": "how to register fastmcp tool",
            "planner_regex_source": None,
            "planner_anchor_terms": ["FastMCP", "tool"],
            "planner_concept_terms": ["tool", "registration"],
            "planner_source_tokens": {"tokens": ["FastMCP", "tool"]},
            "planner_qualifiers": {"lang": "python"},
            "planner_warnings": [],
            "planner_backend_channels": ["github", "sourcegraph", "grepapp"],
            "planner_variants": ["FastMCP.tool", "def tool"],
            "planner_variant_kinds": ["symbol", "pattern"],
            "provider_response_count": 3,
            "provider_hit_counts": {"github": 5, "sourcegraph": 3, "grepapp": 2},
            "request_count": 3,
            "hydration_count": 2,
            "rerank_count": 5,
            "returned_count": 2,
            "repository_count": 1,
            "diagnostic_count": 1,
            "truncated": False,
            "dropped_count": 0,
            "estimated_output_tokens": 850,
            "duration_ms": 450.0,
            "outcome": "ok",
            "error_type": None,
            "error_message": None,
            "payload_json": {"query": "FastMCP.tool registration"},
        }
        provider_rows = [
            {
                "terminal_event_id": "csr-term-1",
                "response_index": 0,
                "provider": "github",
                "hit_count": 5,
                "request_count": 1,
                "outcome": "ok",
                "compiled_queries": ["repo:prefecthq/fastmcp FastMCP.tool"],
                "duration_ms": 150.0,
                "error_type": None,
                "error_message": None,
                "payload_json": {},
            },
            {
                "terminal_event_id": "csr-term-1",
                "response_index": 1,
                "provider": "grepapp",
                "hit_count": 2,
                "request_count": 1,
                "outcome": "ok",
                "compiled_queries": ["FastMCP.tool"],
                "duration_ms": 120.0,
                "error_type": None,
                "error_message": None,
                "payload_json": {},
            },
        ]
        diagnostic_rows = [
            {
                "terminal_event_id": "csr-term-1",
                "diagnostic_index": 0,
                "provider": "sourcegraph",
                "outcome": "partial",
                "failure_kind": "rate_limit",
                "message": "Secondary rate limit encountered",
                "status_code": 429,
                "retry_after_seconds": 5.0,
                "query": "FastMCP.tool",
                "details": {"retry": True},
            }
        ]
        hit_rows = [
            {
                "terminal_event_id": "csr-term-1",
                "hit_rank": 1,
                "url": "https://github.com/prefecthq/fastmcp/blob/main/src/fastmcp/server.py",
                "repository": "prefecthq/fastmcp",
                "path": "src/fastmcp/server.py",
                "sha": "abc1234",
                "provider": "github",
                "query_variant": "FastMCP.tool",
                "search_rank": 1,
                "result_kind": "code_match",
                "evidence_role": "implementation",
                "title": "FastMCP Server Implementation",
                "snippet": "@mcp.tool()\ndef search(): pass",
                "published_date": "2026-02-01",
                "final_score": 0.95,
                "score_components": {"exact_symbol": 0.5, "symbol_match": 0.3, "path_relevance": 0.15},
                "reasons": ["exact symbol match"],
                "hydrated": True,
                "hydrated_source_truncated": False,
                "line_start": 45,
                "line_end": 60,
                "commit_oid": "abc1234567890",
                "fragment_count": 1,
                "symbol_count": 2,
                "match_span_count": 1,
                "location_precision": "line",
                "lines_available": True,
                "revision_available": True,
                "match_data_available": True,
                "source_metadata": {"lang": "python"},
                "payload_json": None,
            },
            {
                "terminal_event_id": "csr-term-1",
                "hit_rank": 2,
                "url": "https://github.com/prefecthq/fastmcp/blob/main/src/fastmcp/tools.py",
                "repository": "prefecthq/fastmcp",
                "path": "src/fastmcp/tools.py",
                "sha": "def5678",
                "provider": "grepapp",
                "query_variant": "def tool",
                "search_rank": 2,
                "result_kind": "code_match",
                "evidence_role": "implementation",
                "title": "Tools module",
                "snippet": "class ToolRegistry: pass",
                "published_date": "2026-02-01",
                "final_score": 0.82,
                "score_components": {"exact_symbol": 0.3, "symbol_match": 0.4, "path_relevance": 0.12},
                "reasons": ["symbol match"],
                "hydrated": False,
                "hydrated_source_truncated": False,
                "line_start": 10,
                "line_end": 25,
                "commit_oid": "def5678901234",
                "fragment_count": 1,
                "symbol_count": 1,
                "match_span_count": 1,
                "location_precision": "line",
                "lines_available": True,
                "revision_available": True,
                "match_data_available": True,
                "source_metadata": {"lang": "python"},
                "payload_json": None,
            },
        ]
        hit_variant_rows = [
            {
                "terminal_event_id": "csr-term-1",
                "hit_rank": 1,
                "association_index": 0,
                "variant_index": 0,
                "provider": "github",
                "query_variant": "FastMCP.tool",
                "search_rank": 1,
            }
        ]
        query_variant_rows = [
            {
                "terminal_event_id": "csr-term-1",
                "variant_index": 0,
                "query_text": "FastMCP.tool",
                "variant_kind": "symbol",
            },
            {
                "terminal_event_id": "csr-term-1",
                "variant_index": 1,
                "query_text": "def tool",
                "variant_kind": "pattern",
            },
        ]
        repo_rows = [
            {
                "terminal_event_id": "csr-term-1",
                "repository_index": 0,
                "name_with_owner": "prefecthq/fastmcp",
                "url": "https://github.com/prefecthq/fastmcp",
                "description": "FastMCP Server Framework",
                "stars": 1200,
                "forks": 150,
                "pushed_at": "2026-02-15",
                "language": "Python",
                "topics": ["mcp", "python", "fastmcp"],
                "license_spdx_id": "Apache-2.0",
                "homepage_url": "https://fastmcp.org",
                "default_branch": "main",
                "head_oid": "abc1234567890",
                "archived": False,
                "fork": False,
                "discovery_rank": 1,
                "discovery_score": 0.98,
                "discovery_queries": ["fastmcp"],
                "proof_hits": 2,
                "proof_paths": ["src/fastmcp/server.py", "src/fastmcp/tools.py"],
                "proof_providers": ["github", "grepapp"],
                "verified": True,
                "payload_json": None,
            }
        ]
        rerank_rows = [
            {
                "terminal_event_id": "csr-term-1",
                "provider": "cloud_rerank",
                "model": "cloud_reranker",
                "input_count": 5,
                "output_count": 2,
                "reranked_count": 5,
                "status": "success",
                "diagnostic_outcome": None,
                "diagnostic_message": None,
                "duration_ms": 85.0,
                "payload_json": None,
            }
        ]

        insert_code_search_batches(
            code_search_runs=[run_row],
            code_search_providers=provider_rows,
            code_search_diagnostics=diagnostic_rows,
            code_search_hits=hit_rows,
            code_search_hit_variants=hit_variant_rows,
            code_search_query_variants=query_variant_rows,
            code_search_repositories=repo_rows,
            code_search_rerank=rerank_rows,
            db_path=str(self.db_path),
        )
        drain_duckdb_writes()

        ensure_views(db_path=str(self.db_path))

        con = duckdb.connect(str(self.db_path), read_only=True)
        yield_rows = con.execute("SELECT provider, outcome, total_responses, total_hits_returned FROM vw_code_search_provider_yield ORDER BY provider").fetchall()
        self.assertEqual(len(yield_rows), 2)
        self.assertEqual(yield_rows[0][0], "github")
        self.assertEqual(yield_rows[0][3], 5)

        hit_sources = con.execute("SELECT provider, total_hits, hydrated_hits, avg_final_score FROM vw_code_search_hit_sources ORDER BY provider").fetchall()
        self.assertEqual(len(hit_sources), 2)

        variants = con.execute("SELECT query_variant, provider, runs_with_variant_hit, total_associated_hits FROM vw_code_search_variant_effectiveness").fetchall()
        self.assertEqual(len(variants), 1)

        rerank = con.execute("SELECT provider, status, total_executions, total_input_hits, total_output_hits FROM vw_code_search_rerank_execution").fetchall()
        self.assertEqual(len(rerank), 1)

        diags = con.execute("SELECT provider, outcome, failure_kind, diagnostic_count FROM vw_code_search_diagnostic_patterns").fetchall()
        self.assertEqual(len(diags), 1)

        repos = con.execute("SELECT language, verified, discovered_repo_count, avg_stars FROM vw_code_search_repository_discovery").fetchall()
        self.assertEqual(len(repos), 1)

        scores = con.execute("SELECT provider, hit_count, avg_final_score, avg_exact_symbol_score FROM vw_code_search_score_component_distribution ORDER BY provider").fetchall()
        self.assertEqual(len(scores), 2)
        con.close()

    def test_content_operations_fetches_summaries_persistence_and_views(self) -> None:
        ensure_store_schema(db_path=str(self.db_path))

        op_row = {
            "terminal_event_id": "co-term-1",
            "tool_call_id": "co-tool-1",
            "trace_id": "trace-content",
            "session_id": "sess-content",
            "tool_name": "get_content",
            "input_count": 1,
            "output_count": 1,
            "duration_ms": 250.0,
            "status": "success",
            "error_type": None,
            "error_message": None,
            "payload_json": {"url": "https://duckdb.org"},
        }
        fetch_rows = [
            {
                "terminal_event_id": "co-term-1",
                "tool_call_id": "co-tool-1",
                "item_index": 0,
                "input_url": "https://duckdb.org",
                "normalized_url": "https://duckdb.org",
                "fetched_url": "https://duckdb.org/docs",
                "source_type": "html",
                "fetch_backend": "trafilatura",
                "status": "success",
                "content_length": 15000,
                "page_char_count": 15000,
                "word_count": 2200,
                "window_offset": 0,
                "window_length": 20000,
                "window_returned_chars": 15000,
                "window_total_chars": 15000,
                "window_has_more": False,
                "window_next_offset": None,
                "item_duration_ms": 250.0,
                "payload_json": {},
            }
        ]
        summary_rows = [
            {
                "terminal_event_id": "co-term-1",
                "tool_call_id": "co-tool-1",
                "item_index": 0,
                "normalized_url": "https://duckdb.org",
                "focus_query": "analytics",
                "input_chars": 15000,
                "source_url_count": 1,
                "is_batch": False,
                "batch_size": 1,
                "is_stub": False,
                "backend": "gemini-3.5-flash-lite",
                "model_requested": "gemini-3.5-flash-lite",
                "model_used": "gemini-3.5-flash-lite",
                "fallback_attempted": False,
                "fallback_tier": 0,
                "input_tokens": 3000,
                "output_tokens": 450,
                "total_tokens": 3450,
                "summary_length_chars": 800,
                "key_points_count": 5,
                "important_entities_count": 3,
                "verbatim_terms_count": 4,
                "limitations_count": 1,
                "source_date": "2026-02-01",
                "status": "success",
                "error_type": None,
                "error_message": None,
                "duration_ms": 650.0,
                "payload_json": {"summary": "DuckDB is an in-process SQL OLAP database."},
            }
        ]

        insert_content_operation_batches(
            content_operations=[op_row],
            content_fetches=fetch_rows,
            content_summaries=summary_rows,
            db_path=str(self.db_path),
        )
        drain_duckdb_writes()

        ensure_views(db_path=str(self.db_path))

        con = duckdb.connect(str(self.db_path), read_only=True)
        fetch_perf = con.execute(
            "SELECT tool_name, fetch_backend, total_fetches, avg_content_length FROM vw_content_fetch_performance"
        ).fetchone()
        self.assertIsNotNone(fetch_perf)
        assert fetch_perf is not None
        self.assertEqual(fetch_perf[0], "get_content")
        self.assertEqual(fetch_perf[1], "trafilatura")
        self.assertEqual(fetch_perf[2], 1)

        summary_signals = con.execute(
            "SELECT backend, is_batch, total_summaries, avg_summary_chars, avg_key_points FROM vw_content_summary_output_signals"
        ).fetchone()
        self.assertIsNotNone(summary_signals)
        assert summary_signals is not None
        self.assertEqual(summary_signals[0], "gemini-3.5-flash-lite")
        self.assertEqual(summary_signals[2], 1)
        self.assertEqual(summary_signals[4], 5.0)
        batch_vs_single = con.execute("SELECT tool_name, is_batch, total_operations, total_summary_items FROM vw_content_summary_batch_vs_single").fetchall()
        self.assertEqual(len(batch_vs_single), 1)

        fallbacks = con.execute("SELECT terminal_event_id, backend, fallback_attempted FROM vw_content_summary_fallbacks").fetchall()
        self.assertEqual(len(fallbacks), 1)

        focus = con.execute("SELECT focus_mode, is_batch, total_summaries FROM vw_content_summary_focus_comparison").fetchall()
        self.assertEqual(len(focus), 1)
        self.assertEqual(focus[0][0], "focused")

        tokens = con.execute("SELECT backend, total_summaries, known_input_tokens, known_output_tokens, known_total_tokens FROM vw_content_summary_daily_tokens").fetchone()
        self.assertIsNotNone(tokens)
        assert tokens is not None
        self.assertEqual(tokens[2], 3000)
        self.assertEqual(tokens[3], 450)
        self.assertEqual(tokens[4], 3450)

    def test_all_22_views_execute_without_error(self) -> None:
        ensure_store_schema(db_path=str(self.db_path))
        ensure_views(db_path=str(self.db_path))

        con = duckdb.connect(str(self.db_path), read_only=True)
        view_names = [
            # Cross-tool
            "vw_tool_call_coverage",
            "vw_tool_call_linkage_gaps",
            "vw_web_search_tool_linkage",
            # Quick search
            "vw_quick_web_search_performance",
            "vw_quick_web_search_citation_sources",
            # Gemini search
            "vw_gemini_search_performance",
            "vw_gemini_search_fallbacks",
            "vw_gemini_search_sources",
            # Code search
            "vw_code_search_provider_yield",
            "vw_code_search_hit_sources",
            "vw_code_search_variant_effectiveness",
            "vw_code_search_rerank_execution",
            "vw_code_search_diagnostic_patterns",
            "vw_code_search_repository_discovery",
            "vw_code_search_score_component_distribution",
            # Content & summary
            "vw_content_fetch_performance",
            "vw_content_summary_output_signals",
            "vw_content_summary_attempt_performance",
            "vw_content_summary_batch_vs_single",
            "vw_content_summary_fallbacks",
            "vw_content_summary_focus_comparison",
            "vw_content_summary_daily_tokens",
        ]

        for name in view_names:
            try:
                res = con.execute(f"SELECT * FROM {name} LIMIT 5").fetchall()
                self.assertIsInstance(res, list, f"Query on view {name} failed to return a list")
            except Exception as exc:
                self.fail(f"View {name} failed to execute: {exc}")
        con.close()

    def test_observability_event_propagation_and_matching_terminal_event_id(self) -> None:
        ensure_store_schema(db_path=str(self.db_path))
        logger = logging.getLogger("test_obs")
        logger.addHandler(logging.NullHandler())

        with patch.object(settings, "analytics_duckdb_path", str(self.db_path)):
            emit_tool_observability_event(
                logger,
                "quick_web_search",
                "response",
                tool_call_id="tool-qws-999",
                search_queries=["parallel ai search"],
                objective="Quick recon",
                citations=[{"title": "Parallel", "url": "https://parallel.ai", "snippet": "Parallel AI", "publish_date": "2026-01-01", "excerpts": ["AI"]}],
                total_citations=1,
                duration_ms=210.0,
            )
            # Gemini search
            emit_tool_observability_event(
                logger,
                "gemini_search",
                "response",
                tool_call_id="tool-gsr-999",
                query="gemini test query",
                mode="structured",
                answer="grounded answer",
                model_used="gemini-3.5-flash-lite",
                prompt_tokens=150,
                completion_tokens=40,
                total_tokens=190,
                sources=[{"title": "Source 1", "url": "https://example.com/1"}],
                duration_ms=350.0,
            )
            # Content
            emit_tool_observability_event(
                logger,
                "get_content",
                "response",
                tool_call_id="tool-get-999",
                url="https://example.com/doc",
                canonical_url="https://example.com/doc",
                source_type="html",
                fetch_backend="trafilatura",
                content_length=5000,
                page_char_count=5000,
                word_count=800,
                duration_ms=180.0,
            )
            # Code search
            emit_tool_observability_event(
                logger,
                "code_search",
                "response",
                tool_call_id="tool-code-999",
                query="FastMCP.tool",
                channels=["github"],
                outcome="ok",
                providers={"github": 2},
                output_count=2,
                duration_ms=410.0,
            )
            drain_duckdb_writes()

        con = duckdb.connect(str(self.db_path), read_only=True)
        # Check tool_calls
        tc_rows = con.execute("SELECT event_id, tool_name, tool_call_id FROM tool_calls").fetchall()
        self.assertEqual(len(tc_rows), 4)
        event_by_tool = {row[1]: (row[0], row[2]) for row in tc_rows}

        # Check quick search terminal_event_id
        qws_row = con.execute("SELECT terminal_event_id, tool_call_id FROM quick_web_search_runs").fetchone()
        self.assertIsNotNone(qws_row)
        assert qws_row is not None
        self.assertEqual(qws_row[0], event_by_tool["quick_web_search"][0])
        self.assertEqual(qws_row[1], event_by_tool["quick_web_search"][1])

        # Check gemini search terminal_event_id
        gsr_row = con.execute("SELECT terminal_event_id, tool_call_id FROM gemini_search_runs").fetchone()
        self.assertIsNotNone(gsr_row)
        assert gsr_row is not None
        self.assertEqual(gsr_row[0], event_by_tool["gemini_search"][0])
        self.assertEqual(gsr_row[1], event_by_tool["gemini_search"][1])

        # Check content operation terminal_event_id
        co_row = con.execute("SELECT terminal_event_id, tool_call_id FROM content_operations").fetchone()
        self.assertIsNotNone(co_row)
        assert co_row is not None
        self.assertEqual(co_row[0], event_by_tool["get_content"][0])
        self.assertEqual(co_row[1], event_by_tool["get_content"][1])

        # Check code search run terminal_event_id
        csr_row = con.execute("SELECT terminal_event_id, tool_call_id FROM code_search_runs").fetchone()
        self.assertIsNotNone(csr_row)
        assert csr_row is not None
        self.assertEqual(csr_row[0], event_by_tool["code_search"][0])
        self.assertEqual(csr_row[1], event_by_tool["code_search"][1])

    def test_reports_and_query_plans(self) -> None:
        ensure_store_schema(db_path=str(self.db_path))

        reports = available_reports()
        self.assertIn("tool-call-coverage", reports)
        self.assertIn("quick-search-performance", reports)
        self.assertIn("gemini-search-performance", reports)
        self.assertIn("code-search-provider-yield", reports)
        self.assertIn("code-search-hit-sources", reports)
        self.assertIn("content-fetch-performance", reports)
        self.assertIn("content-summary-output-signals", reports)

        for rep in ("tool-call-coverage", "quick-search-performance", "gemini-search-performance", "code-search-provider-yield", "code-search-hit-sources", "content-fetch-performance", "content-summary-output-signals"):
            table = run_report(rep, days=7, db_path=str(self.db_path))
            self.assertIsNotNone(table)

        # Test query plans
        q_code = build_analytics_query_plan("how is code search performing across grepapp and sourcegraph?")
        self.assertEqual(q_code.rationale, "code_search")

        q_quick = build_analytics_query_plan("show quick search citations and latency")
        self.assertEqual(q_quick.rationale, "quick_search")

        q_gemini = build_analytics_query_plan("what are the gemini grounding queries and tokens?")
        self.assertEqual(q_gemini.rationale, "gemini_search")

        q_content = build_analytics_query_plan("what are the summary tokens and content fetch backends?")
        self.assertEqual(q_content.rationale, "content_summaries")

        q_cov = build_analytics_query_plan("show cross tool call coverage and linkage")
        self.assertEqual(q_cov.rationale, "tool_call_coverage")


if __name__ == "__main__":
    unittest.main()
