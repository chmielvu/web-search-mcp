from __future__ import annotations

import json
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from kindly_web_search_mcp_server.cli.app import app


runner = CliRunner()


def _payload(result) -> dict:
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def test_search_quick_emits_json_payload(monkeypatch) -> None:
    mock_payload = AsyncMock(
        return_value={
            "search_queries": ["fastmcp transport", "mcp protocol"],
            "citations": [{"title": "FastMCP Transports", "url": "https://example.com"}],
            "total_citations": 1,
        }
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.quick_search.fetch_quick_web_search_payload",
        mock_payload,
    )

    payload = _payload(
        runner.invoke(
            app,
            [
                "search",
                "quick",
                "--search-query",
                "fastmcp transport",
                "--search-query",
                "mcp protocol",
                "--objective",
                "test research goal",
            ],
        )
    )

    mock_payload.assert_awaited_once_with(
        ["fastmcp transport", "mcp protocol"], "test research goal"
    )
    assert payload["meta"]["command"] == "search quick"
    assert payload["data"]["total_citations"] == 1


def test_search_quick_missing_objective_fails(monkeypatch) -> None:
    mock_payload = AsyncMock()
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.quick_search.fetch_quick_web_search_payload",
        mock_payload,
    )
    result = runner.invoke(app, ["search", "quick", "--search-query", "test"])
    assert result.exit_code != 0
    mock_payload.assert_not_awaited()


def test_search_quick_missing_search_query_fails(monkeypatch) -> None:
    mock_payload = AsyncMock()
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.quick_search.fetch_quick_web_search_payload",
        mock_payload,
    )
    result = runner.invoke(app, ["search", "quick", "--objective", "has objective"])
    assert result.exit_code != 0
    mock_payload.assert_not_awaited()


def test_content_fetch_emits_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.content.fetch_payload",
        AsyncMock(
            return_value={
                "mode": "single",
                "results": [
                    {
                        "input_url": "https://example.com/docs",
                        "normalized_url": "https://example.com/docs",
                        "url": "https://example.com/docs",
                        "status": "success",
                        "source_type": "html",
                        "fetch_backend": "safe_http_extract",
                        "page_content": "# Example docs",
                        "window": {"has_more": False},
                    }
                ],
            }
        ),
    )

    payload = _payload(
        runner.invoke(
            app,
            [
                "content",
                "fetch",
                "--url",
                "https://example.com/docs",
            ],
        )
    )

    assert payload["meta"]["command"] == "content fetch"
    assert payload["data"]["mode"] == "single"
    assert payload["data"]["results"][0]["status"] == "success"
    assert payload["data"]["results"][0]["page_content"] == "# Example docs"
    assert payload["data"]["results"][0]["window"]["has_more"] is False
