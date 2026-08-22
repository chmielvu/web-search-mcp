from __future__ import annotations

import logging
from types import SimpleNamespace

import duckdb

from kindly_web_search_mcp_server.analytics.async_writes import drain_duckdb_writes
from kindly_web_search_mcp_server.analytics.duckdb_store import (
    ensure_store_schema,
    insert_funnel_uplift_batches,
)
from kindly_web_search_mcp_server.settings import settings
from kindly_web_search_mcp_server.tools.code_search.models import (
    CodeSearchHit,
    CodeSearchRequest,
    CodeSearchResultType,
    QueryMetadata,
    Stats,
)
from kindly_web_search_mcp_server.utils.observability import (
    _persist_code_search_analytics,
    emit_tool_observability_event,
)


def test_response_producers_persist_full_fields_and_batch_output_links(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "analytics.duckdb"
    ensure_store_schema(db_path=str(db_path))
    monkeypatch.setattr(settings, "analytics_duckdb_path", str(db_path))
    logger = logging.getLogger("test.analytics.producers")

    emit_tool_observability_event(
        logger,
        "gemini_search",
        "response",
        tool_call_id="gemini-tool-1",
        query="duckdb vss",
        research_goal="understand vector search",
        session_id="session-1",
        structured_output=True,
        mode="structured",
        answer="DuckDB has an experimental VSS extension.",
        structured_data={"extension": "vss"},
        sources=[{"url": "https://duckdb.org/docs/current/core_extensions/vss", "title": "VSS"}],
        url_citations=[{"url": "https://duckdb.org/blog", "title": "Blog"}],
        search_queries=["duckdb vss"],
        model="gemini-test",
        model_used="gemini-test",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        grounding_chunks_count=1,
        web_search_queries_count=1,
        fallback_chain=["gemini-test"],
        fallback_reason=None,
        output_count=2,
        duration_ms=125.0,
    )
    emit_tool_observability_event(
        logger,
        "fetch",
        "response",
        tool_call_id="content-tool-1",
        session_id="session-1",
        results=[
            {
                "input_url": "https://example.com/a",
                "normalized_url": "https://example.com/a",
                "content_type": "text/html",
                "cached": True,
                "status": "success",
            },
            {
                "input_url": "https://example.com/b",
                "normalized_url": "https://example.com/b",
                "content_type": "text/markdown",
                "cached": False,
                "status": "success",
            },
        ],
        duration_ms=80.0,
    )
    drain_duckdb_writes()

    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        gemini = connection.execute(
            """
            SELECT mode, answer, model_used, prompt_tokens, completion_tokens,
                   total_tokens, duration_ms
            FROM gemini_search_runs
            WHERE tool_call_id = 'gemini-tool-1'
            """
        ).fetchone()
        assert gemini == (
            "structured",
            "DuckDB has an experimental VSS extension.",
            "gemini-test",
            10,
            20,
            30,
            125.0,
        )
        source_count = connection.execute(
            "SELECT count() FROM gemini_search_sources WHERE tool_call_id = 'gemini-tool-1'"
        ).fetchone()
        assert source_count is not None and source_count[0] == 2
        content = connection.execute(
            """
            SELECT item_index, input_url, content_type, cached
            FROM content_fetches
            WHERE tool_call_id = 'content-tool-1'
            ORDER BY item_index
            """
        ).fetchall()
        assert content == [
            (0, "https://example.com/a", "text/html", True),
            (1, "https://example.com/b", "text/markdown", False),
        ]
        output_items = connection.execute(
            """
            SELECT item_rank, raw_url
            FROM tool_output_items
            WHERE tool_call_id = 'content-tool-1'
            ORDER BY item_rank
            """
        ).fetchall()
        assert output_items == [
            (1, "https://example.com/a"),
            (2, "https://example.com/b"),
        ]
    finally:
        connection.close()


def test_code_search_analytics_uses_provider_and_rerank_metadata(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "analytics.duckdb"
    ensure_store_schema(db_path=str(db_path))
    monkeypatch.setattr(settings, "analytics_duckdb_path", str(db_path))
    request = CodeSearchRequest(query="retry backoff")
    plan = SimpleNamespace(
        original_query="retry backoff",
        search_text="retry backoff",
        api_query="retry backoff",
        mode="code",
        anchor_terms=[],
        variants=[],
        metadata=SimpleNamespace(
            structural_kind=None,
            exa_semantic_query=None,
            regex_source=None,
            concept_terms=[],
            source_tokens=[],
            qualifiers={},
            warnings=[],
            backend_channels=[],
            variant_kinds=[],
        ),
    )
    stats = Stats(
        provider_counts={"github": 1, "sourcegraph": 0},
        request_count=2,
        returned_count=1,
        rerank_count=1,
        rerank_provider="cohere",
        rerank_model="rerank-v4",
        rerank_status="success",
        rerank_duration_ms=42.0,
        rerank_input_count=2,
        rerank_output_count=1,
        rerank_payload={"profile": "code", "blend_weight": 0.2},
    )
    response = CodeSearchResultType(
        query="retry backoff",
        outcome="ok",
        results=[CodeSearchHit(url="https://github.com/acme/repo/blob/main/retry.py", provider="github")],
        repositories=[],
        diagnostics=[],
        stats=stats,
        query_metadata=QueryMetadata(original_query="retry backoff"),
        provider_summaries=[
            {
                "provider": "github",
                "hit_count": 1,
                "request_count": 1,
                "outcome": "ok",
                "compiled_queries": ["retry backoff"],
                "payload_json": {"channel": "code"},
            },
            {
                "provider": "sourcegraph",
                "hit_count": 0,
                "request_count": 1,
                "outcome": "partial",
                "compiled_queries": ["retry backoff"],
                "payload_json": {"channel": "code"},
            },
        ],
    )

    _persist_code_search_analytics(
        terminal_event_id="code-terminal-1",
        tool_call_id="code-tool-1",
        fields={"request": request, "plan": plan, "response": response, "duration_ms": 100.0},
        payload={},
        trace_context={},
        status="success",
        error_message=None,
        payload_json={},
        logger=logging.getLogger("test.analytics.code-search"),
    )
    drain_duckdb_writes()

    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        providers = connection.execute(
            """
            SELECT provider, hit_count, request_count, outcome
            FROM code_search_providers
            WHERE terminal_event_id = 'code-terminal-1'
            ORDER BY response_index
            """
        ).fetchall()
        assert providers == [("github", 1, 1, "ok"), ("sourcegraph", 0, 1, "partial")]
        rerank = connection.execute(
            """
            SELECT provider, model, input_count, output_count, reranked_count,
                   status, duration_ms
            FROM code_search_rerank
            WHERE terminal_event_id = 'code-terminal-1'
            """
        ).fetchone()
        assert rerank == ("cohere", "rerank-v4", 2, 1, 1, "success", 42.0)
    finally:
        connection.close()


def test_async_rerank_writer_persists_candidate_stage_events(tmp_path, monkeypatch) -> None:
    import asyncio

    from kindly_web_search_mcp_server.models import WebSearchResult
    from kindly_web_search_mcp_server.rerank.observability import (
        record_rerank_candidate_rows_async,
    )

    db_path = tmp_path / "analytics.duckdb"
    ensure_store_schema(db_path=str(db_path))
    monkeypatch.setattr(settings, "analytics_duckdb_path", str(db_path))
    before = [
        WebSearchResult(
            link="https://example.com/a",
            title="A",
            snippet="before",
            score=0.4,
        ),
        WebSearchResult(
            link="https://example.com/b",
            title="B",
            snippet="before",
            score=0.3,
        ),
    ]
    after = [before[0]]

    asyncio.run(
        record_rerank_candidate_rows_async(
            logging.getLogger("test.analytics.rerank"),
            run_key="run-rerank-1",
            stage="cross_encoder",
            before_candidates=before,
            after_candidates=after,
        )
    )
    drain_duckdb_writes()

    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT canonical_result_id, entered, survived, removal_reason
            FROM candidate_stage_events
            WHERE run_key = 'run-rerank-1'
            ORDER BY survived DESC, canonical_result_id
            """
        ).fetchall()
        assert len(rows) == 2
        assert any(row[2] is True for row in rows)
        assert any(row[2] is False and row[3] == "rerank_stage_removed" for row in rows)
    finally:
        connection.close()

def test_result_catalog_upsert_tracks_cross_run_appearances(tmp_path) -> None:
    db_path = tmp_path / "analytics.duckdb"
    ensure_store_schema(db_path=str(db_path))
    row = {
        "canonical_result_id": "result-1",
        "canonical_url": "https://example.com/article",
        "domain": "example.com",
        "title_first_seen": "Article",
        "first_seen_run_key": "run-1",
        "total_run_appearances": 1,
    }
    insert_funnel_uplift_batches(result_catalog=[row], db_path=str(db_path))
    insert_funnel_uplift_batches(
        result_catalog=[{**row, "first_seen_run_key": "run-2"}],
        db_path=str(db_path),
    )

    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        assert connection.execute(
            "SELECT first_seen_run_key, total_run_appearances FROM result_catalog"
        ).fetchone() == ("run-1", 2)
    finally:
        connection.close()
