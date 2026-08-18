"""Tests verifying semantic bug fixes discovered during python code review."""

import pytest
import duckdb
from kindly_web_search_mcp_server.tools._helpers import _search_history_snapshot
from kindly_web_search_mcp_server.search.providers.brave import suggest_brave_queries
from kindly_web_search_mcp_server.inference.chain import register_chain
from kindly_web_search_mcp_server.rerank.stages import apply_ranked_results
from kindly_web_search_mcp_server.models import WebSearchResult


def test_search_history_snapshot_columns(tmp_path, monkeypatch):
    db_file = tmp_path / "test_events.duckdb"
    conn = duckdb.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE search_runs (
            recorded_at TIMESTAMP,
            run_key VARCHAR,
            query VARCHAR,
            normalized_query VARCHAR,
            intent VARCHAR,
            status VARCHAR,
            duration_ms DOUBLE,
            final_result_count INT,
            selected_providers VARCHAR,
            branch_count INT,
            provider_count INT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO search_runs VALUES (
            CURRENT_TIMESTAMP, 'rk1', 'q1', 'nq1', 'tech', 'success', 12.5, 5, 'brave', 1, 1
        )
        """
    )
    conn.close()

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.tools._helpers.settings.analytics_duckdb_path",
        str(db_file),
    )

    result = _search_history_snapshot(limit=10)
    assert result["count"] == 1
    row = result["search_history"][0]
    assert row["run_key"] == "rk1"
    assert row["query"] == "q1"
    assert row["intent"] == "tech"
    assert row["status"] == "success"
    assert row["duration_ms"] == 12.5
    assert row["final_result_count"] == 5


@pytest.mark.asyncio
async def test_suggest_brave_queries_none_api_key(monkeypatch):
    monkeypatch.delenv("BRAVE_SUGGEST_API_KEY", raising=False)
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.providers.brave.settings.brave_suggest_api_key",
        None,
    )
    res = await suggest_brave_queries("python test")
    assert res == {"suggestions": [], "entities": []}


def test_register_chain_empty_models_validation():
    with pytest.raises(ValueError, match="must have at least one model spec ID"):
        register_chain("test_empty_chain", [])


def test_apply_ranked_results_out_of_bounds_index():
    class DummyRankedResult:
        def __init__(self, index, score):
            self.index = index
            self.score = score

    candidates = [
        WebSearchResult(title="T1", link="http://example.com/1", snippet="S1"),
        WebSearchResult(title="T2", link="http://example.com/2", snippet="S2"),
    ]
    # Pass index 99 which is out of bounds
    invalid_ranked = [DummyRankedResult(index=99, score=0.9)]
    res_candidates, scores, max_s, avg_s = apply_ranked_results(candidates, invalid_ranked)
    assert res_candidates == candidates
    assert scores == []
