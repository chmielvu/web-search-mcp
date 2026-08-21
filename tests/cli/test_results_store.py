from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from kindly_web_search_mcp_server.cli.services.results import (
    TTL_SECONDS,
    cleanup_expired_results,
    persist_cli_result,
    search_results,
    store_result,
)
from kindly_web_search_mcp_server.middleware.result_persistence import (
    ResultPersistenceMiddleware,
)


def test_result_store_enforces_ttl_by_kind(tmp_path) -> None:
    database = tmp_path / "jobs.sqlite"

    cli = store_result("cli", "search web", {"query": "sqlite WAL"}, db_path=database, created_at=100)
    deep = store_result(
        "deep_research",
        "research deep",
        {"query": "SQLite", "answer": "WAL"},
        db_path=database,
        created_at=100,
    )

    assert cli["expires_at"] is not None
    assert deep["expires_at"] is None
    assert search_results("WAL", db_path=database, now=100)[0]["result_kind"] == "deep_research"
    assert search_results("sqlite", db_path=database, result_kind="cli", now=100)

    assert cleanup_expired_results(db_path=database, now=100 + TTL_SECONDS) == 1
    assert search_results("SQLite", db_path=database, now=100 + TTL_SECONDS) == [
        {
            "result_id": deep["result_id"],
            "result_kind": "deep_research",
            "source": "research deep",
            "created_at": "1970-01-01T00:01:40Z",
            "expires_at": None,
            "payload": {"answer": "WAL", "query": "SQLite"},
        }
    ]

def test_cli_output_uses_cli_result_persistence(capsys) -> None:
    from kindly_web_search_mcp_server.cli.output import emit_json

    with patch(
        "kindly_web_search_mcp_server.cli.services.results.persist_cli_result"
    ) as persist:
        emit_json({"query": "sqlite"}, command="search web")

    assert "sqlite" in capsys.readouterr().out
    persist.assert_called_once_with("search web", {"query": "sqlite"})


def test_cli_deep_research_result_has_no_ttl(tmp_path) -> None:
    database = tmp_path / "deep.sqlite"

    stored = persist_cli_result(
        "research deep",
        {"query": "durable research", "report_markdown": "# Report"},
        db_path=database,
    )

    assert stored is not None
    assert stored["result_kind"] == "deep_research"
    assert stored["expires_at"] is None
    assert search_results(
        "durable research", result_kind="deep_research", db_path=database
    )



def test_result_search_filters_source_and_orders_newest_first(tmp_path) -> None:
    database = tmp_path / "results.sqlite"
    store_result("mcp", "web_search", {"query": "older", "results": []}, db_path=database, created_at=10)
    newest = store_result(
        "mcp", "web_search", {"query": "newer", "results": []}, db_path=database, created_at=20
    )
    store_result("cli", "content get", {"query": "newer"}, db_path=database, created_at=30)

    found = search_results("newer", result_kind="mcp", source="web_search", db_path=database, now=30)
    assert [row["result_id"] for row in found] == [newest["result_id"]]


@pytest.mark.asyncio
async def test_mcp_result_middleware_persists_without_changing_result(tmp_path) -> None:
    context = MiddlewareContext(message=SimpleNamespace(name="deep_research"))
    result = ToolResult(structured_content={"answer": "stored", "references": []})

    with patch(
        "kindly_web_search_mcp_server.middleware.result_persistence.persist_mcp_result"
    ) as persist:
        returned = await ResultPersistenceMiddleware().on_call_tool(
            context, lambda _: _return_result(result)
        )

    assert returned is result
    persist.assert_called_once_with("deep_research", {"answer": "stored", "references": []})


async def _return_result(result: ToolResult) -> ToolResult:
    return result
