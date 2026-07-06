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
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.quick_search.fetch_quick_web_search_payload",
        AsyncMock(
            return_value={
                "query": "latest fastmcp transport docs",
                "answer": "Mocked quick search answer",
                "citations": [
                    {
                        "title": "FastMCP Transports",
                        "url": "https://example.com",
                        "snippet": "Mocked citation snippet",
                    }
                ],
                "total_citations": 1,
            }
        ),
    )

    payload = _payload(
        runner.invoke(
            app,
            ["search", "quick", "--query", "latest fastmcp transport docs"],
        )
    )

    assert payload["meta"]["command"] == "search quick"
    assert payload["data"]["query"] == "latest fastmcp transport docs"
    assert payload["data"]["total_citations"] == 1
    assert payload["data"]["citations"][0]["title"] == "FastMCP Transports"


def test_content_get_emits_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.commands.content.fetch_content_payload",
        AsyncMock(
            return_value={
                "input_url": "https://example.com/docs",
                "normalized_url": "https://example.com/docs",
                "fetched_url": "https://example.com/docs",
                "status": "success",
                "source_type": "html",
                "fetch_backend": "safe_http_extract",
                "page_content": "# Example docs",
                "window": {
                    "offset": 0,
                    "length": 20000,
                    "returned_chars": 14,
                    "total_chars": 14,
                    "has_more": False,
                    "next_offset": None,
                    "continuation_notice": None,
                },
                "content_type": "text/markdown",
            }
        ),
    )

    payload = _payload(
        runner.invoke(
            app,
            [
                "content",
                "get",
                "--url",
                "https://example.com/docs",
                "--char-length",
                "20000",
            ],
        )
    )

    assert payload["meta"]["command"] == "content get"
    assert payload["data"]["input_url"] == "https://example.com/docs"
    assert payload["data"]["status"] == "success"
    assert payload["data"]["page_content"] == "# Example docs"
    assert payload["data"]["window"]["has_more"] is False
