from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from kindly_web_search_mcp_server.cli.app import app, main as cli_main


runner = CliRunner()


def _payload(result) -> dict:
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def test_links_discover_emits_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.link_tools.fetch_discover_links_payload",
        AsyncMock(
            return_value={
                "input_url": "https://example.com",
                "normalized_url": "https://example.com",
                "fetched_url": "https://example.com",
                "source_type": "html",
                "links": [],
                "returned_links": 0,
                "has_more": False,
            }
        ),
    )

    payload = _payload(
        runner.invoke(
            app,
            ["links", "discover", "--url", "https://example.com"],
        )
    )

    assert payload["meta"]["command"] == "links discover"
    assert payload["data"]["source_type"] == "html"


def test_content_batch_emits_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.content_batch.fetch_batch_content_payload",
        AsyncMock(
            return_value={
                "results": [],
                "total_requested": 1,
                "total_returned": 0,
                "total_chars_returned": 0,
                "has_more": False,
                "cursor": None,
            }
        ),
    )

    payload = _payload(
        runner.invoke(
            app,
            [
                "content",
                "batch",
                "--url",
                "https://example.com",
            ],
        )
    )

    assert payload["meta"]["command"] == "content batch"
    assert payload["data"]["total_requested"] == 1


def test_ai_gemini_emits_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.ai.fetch_gemini_search_payload",
        AsyncMock(
            return_value={
                "query": "what is fastmcp",
                "answer": "Mock answer",
                "web_search_queries": ["fastmcp"],
            }
        ),
    )

    payload = _payload(
        runner.invoke(
            app,
            ["ai", "gemini", "--query", "what is fastmcp"],
        )
    )

    assert payload["meta"]["command"] == "ai gemini"
    assert payload["data"]["answer"] == "Mock answer"


def test_ai_grok_emits_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.ai.fetch_grok_search_payload",
        AsyncMock(
            return_value={
                "query": "what is fastmcp",
                "answer": "Mock grok answer",
                "citations": [],
                "model": "x-ai/grok-4.3",
                "search_queries_used": 1,
            }
        ),
    )

    payload = _payload(
        runner.invoke(
            app,
            ["ai", "grok", "--query", "what is fastmcp", "--research-goal", "test"],
        )
    )

    assert payload["meta"]["command"] == "ai grok"
    assert payload["data"]["search_queries_used"] == 1


def test_youtube_search_emits_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.youtube.fetch_youtube_search_payload",
        AsyncMock(
            return_value={
                "query": "fastmcp tutorial",
                "results": [],
                "total_results": 0,
            }
        ),
    )

    payload = _payload(
        runner.invoke(
            app,
            ["youtube", "search", "--query", "fastmcp tutorial"],
        )
    )

    assert payload["meta"]["command"] == "youtube search"
    assert payload["data"]["total_results"] == 0


def test_youtube_transcript_emits_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.youtube.fetch_youtube_transcript_payload",
        AsyncMock(
            return_value={
                "video_id": "abc123def45",
                "video_url": "https://www.youtube.com/watch?v=abc123def45",
                "title": None,
                "transcript_text": "hello world",
                "language": "en",
                "is_translated": False,
                "duration_seconds": 12.0,
                "transcript_segments": None,
                "error": None,
            }
        ),
    )

    payload = _payload(
        runner.invoke(
            app,
            [
                "youtube",
                "transcript",
                "--video-id-or-url",
                "abc123def45",
            ],
        )
    )

    assert payload["meta"]["command"] == "youtube transcript"
    assert payload["data"]["video_id"] == "abc123def45"


def test_analytics_query_emits_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.analytics.queries.run_analytics_query",
        lambda question, scope, max_rows, db_path: {
            "question": question,
            "scope": scope,
            "view_prefix": "main.",
            "rationale": "cache",
            "sql": "select 1",
            "row_count": 0,
            "rows": [],
        },
    )

    payload = _payload(
        runner.invoke(
            app,
            ["analytics", "query", "--question", "cache rates"],
        )
    )

    assert payload["meta"]["command"] == "analytics query"
    assert payload["data"]["rationale"] == "cache"


def test_analytics_report_emits_json_payload(monkeypatch) -> None:
    class _Table:
        num_rows = 1

        @staticmethod
        def to_pylist():
            return [{"report": "fetch-quality"}]

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.analytics.reports.run_report",
        lambda report_name, days, db_path: _Table(),
    )

    payload = _payload(
        runner.invoke(
            app,
            [
                "analytics",
                "report",
                "--report-name",
                "fetch-quality",
            ],
        )
    )

    assert payload["meta"]["command"] == "analytics report"
    assert payload["data"]["row_count"] == 1


def test_experiments_create_requires_config_when_non_interactive(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["--non-interactive", "experiments", "create"])

    assert exc.value.code == 2
    # OTel init may write a banner to stderr; extract the last JSON payload.
    stderr = capsys.readouterr().err
    payload = None
    for line in reversed(stderr.strip().split("\n")):
        try:
            candidate = json.loads(line)
            if isinstance(candidate, dict) and "error" in candidate:
                payload = candidate
                break
        except json.JSONDecodeError:
            continue
    assert payload is not None, f"No JSON error payload found in stderr: {stderr!r}"
    assert payload["error"]["kind"] == "usage_error"
    assert "non-interactive mode" in payload["error"]["message"]


def test_links_similar_and_quick_search_emit_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.link_tools.fetch_similar_links_payload",
        AsyncMock(
            return_value={
                "url": "https://example.com",
                "results": [],
                "total_results": 0,
            }
        ),
    )
    mock_quick = AsyncMock(
        return_value={
            "search_queries": ["example quick search"],
            "citations": [],
            "total_citations": 0,
        }
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.quick_search.fetch_quick_web_search_payload",
        mock_quick,
    )

    similar = _payload(
        runner.invoke(
            app,
            ["links", "similar", "--url", "https://example.com"],
        )
    )
    quick = _payload(
        runner.invoke(
            app,
            [
                "search",
                "quick",
                "--search-query",
                "example quick search",
                "--objective",
                "research goal",
            ],
        )
    )

    mock_quick.assert_awaited_once_with(["example quick search"], "research goal")
    assert similar["meta"]["command"] == "links similar"
    assert quick["meta"]["command"] == "search quick"


def test_server_start_delegates(monkeypatch) -> None:
    called: dict[str, object] = {}

    def _fake_server_main(args):
        called["args"] = args

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.server.main",
        _fake_server_main,
    )

    result = runner.invoke(
        app,
        ["server", "start", "--http", "--port", "8010"],
    )

    assert result.exit_code == 0, result.stderr
    assert called["args"] == ["--http", "--port", "8010"]
