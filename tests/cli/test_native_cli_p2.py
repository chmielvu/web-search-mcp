from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import duckdb
import pytest

from kindly_web_search_mcp_server.cli.services.research_collect import collect_research_bundle
from kindly_web_search_mcp_server.cli.services.search_runs import (
    inspect_search_run,
    postmortem_search_run,
)



def test_search_web_service_returns_run_key(monkeypatch) -> None:
    class _Response:
        def model_dump(self, **_: object) -> dict:
            return {"query": "q", "results": []}

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.search_web.execute_web_search",
        AsyncMock(return_value=(_Response(), object())),
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.search_web.get_http_client",
        AsyncMock(return_value=object()),
    )

    from kindly_web_search_mcp_server.cli.services.search_web import fetch_web_search_payload

    payload = asyncio.run(
        fetch_web_search_payload(
            ["q"],
            rewrite=False,
            research_goal="goal",
        )
    )

    assert isinstance(payload["run_key"], str)
    assert payload["run_key"]


def _seed_run_database(path: Path) -> None:
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE search_runs (
                run_key VARCHAR, recorded_at TIMESTAMP, query VARCHAR,
                research_goal VARCHAR, intent VARCHAR, status VARCHAR, error_type VARCHAR,
                duration_ms DOUBLE, provider_count INTEGER, final_result_count INTEGER,
                selected_providers VARCHAR[], skipped_providers VARCHAR[]
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE search_branches (
                run_key VARCHAR, branch_index INTEGER, branch_role VARCHAR,
                branch_query VARCHAR, branch_why VARCHAR,
                assigned_providers VARCHAR[], attempted_providers VARCHAR[],
                skipped_providers VARCHAR[], results_count INTEGER, latency_ms DOUBLE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE provider_calls (
                run_key VARCHAR, branch_index INTEGER, branch_role VARCHAR,
                provider VARCHAR, status VARCHAR, num_results_returned INTEGER,
                latency_ms DOUBLE, error_type VARCHAR, http_status INTEGER,
                result_class VARCHAR, retry_after_seconds DOUBLE, retryable BOOLEAN,
                recorded_at TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE rerank_stages (
                run_key VARCHAR, stage VARCHAR, provider VARCHAR, model VARCHAR,
                input_count INTEGER, output_count INTEGER, duration_ms DOUBLE,
                status VARCHAR, error_type VARCHAR, recorded_at TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE final_results (
                run_key VARCHAR, rank INTEGER, title VARCHAR, link VARCHAR,
                domain VARCHAR, final_score DOUBLE, providers VARCHAR[], provider_count INTEGER
            )
            """
        )
        connection.execute(
            """
            INSERT INTO search_runs VALUES
            ('run-1', CURRENT_TIMESTAMP, 'q', 'goal', 'general', 'success', NULL, 10.0, 2, 1, ['a'], [])
            """
        )
        connection.execute(
            """
            INSERT INTO provider_calls VALUES
            ('run-1', 0, 'original_free', 'a', 'error', 0, 5.0, 'timeout', 504, 'timeout', 2.0, true, CURRENT_TIMESTAMP)
            """
        )
        connection.execute(
            """
            INSERT INTO final_results VALUES
            ('run-1', 1, 'Title', 'https://example.com', 'example.com', 0.9, ['a'], 1)
            """
        )
    finally:
        connection.close()


def test_search_run_inspection_and_postmortem_are_read_only(tmp_path: Path) -> None:
    database = tmp_path / "analytics.duckdb"
    _seed_run_database(database)

    inspected = inspect_search_run("run-1", db_path=str(database))
    postmortem = postmortem_search_run("run-1", db_path=str(database))

    assert inspected["run"]["run_key"] == "run-1"
    assert inspected["final_results"][0]["link"] == "https://example.com"
    assert postmortem["provider_summary"][0]["provider"] == "a"
    assert postmortem["provider_summary"][0]["errors"] == 1


@pytest.mark.asyncio
async def test_research_collect_writes_bundle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.research_collect.fetch_web_search_payload",
        AsyncMock(
            return_value={
                "run_key": "run-1",
                "results": [{"title": "Source", "link": "https://example.com"}],
            }
        ),
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.research_collect.fetch_batch_content_payload",
        AsyncMock(
            return_value={
                "results": [
                    {
                        "input_url": "https://example.com",
                        "status": "success",
                        "page_content": "# Source\n",
                        "metadata": {"title": "Source"},
                    }
                ],
                "has_more": False,
            }
        ),
    )

    payload = await collect_research_bundle(
        "q",
        "goal",
        output_dir=tmp_path / "bundle",
        top_results=1,
    )

    root = tmp_path / "bundle"
    assert payload["run_key"] == "run-1"
    assert (root / "search.json").exists()
    assert (root / "sources.json").exists()
    assert (root / "report.md").exists()
    assert (root / "manifest.json").exists()
    assert (root / "sources" / "source-001.md").read_text(encoding="utf-8") == "# Source\n"
