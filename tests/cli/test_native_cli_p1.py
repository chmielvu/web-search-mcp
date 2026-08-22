from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from kindly_web_search_mcp_server.cli.app import app


runner = CliRunner()


def _payload(result) -> dict:
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def test_search_quick_forwards_advanced_controls(monkeypatch) -> None:
    fetch = AsyncMock(return_value={"search_queries": ["one"], "citations": []})
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.quick_search.fetch_quick_web_search_payload",
        fetch,
    )

    _payload(
        runner.invoke(
            app,
            [
                "--quiet",
                "search",
                "quick",
                "--search-query",
                "one",
                "--objective",
                "goal",
                "--max-results",
                "7",
                "--include-domain",
                "docs.python.org",
                "--after-date",
                "2026-01-01",
                "--session-id",
                "session-1",
                "--disable-cache-fallback",
            ],
        )
    )

    fetch.assert_awaited_once_with(
        ["one"],
        "goal",
        max_results=7,
        session_id="session-1",
        include_domains=["docs.python.org"],
        after_date="2026-01-01",
        disable_cache_fallback=True,
    )


def test_search_fetch_forwards_snapshot_query(monkeypatch) -> None:
    fetch = AsyncMock(
        return_value={
            "outcome": "ok",
            "repository": "owner/repo",
            "resolved_commit": "a" * 40,
            "content": "def run(): pass",
        }
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.code_fetch.fetch_code_fetch_payload",
        fetch,
    )

    payload = _payload(
        runner.invoke(
            app,
            [
                "--quiet",
                "search",
                "fetch",
                "--repository",
                "owner/repo",
                "--path",
                "src/run.py",
            ],
        )
    )

    fetch.assert_awaited_once_with(
        "owner/repo",
        query=None,
        path="src/run.py",
        symbol=None,
        ref=None,
        regexp=False,
        max_matches=25,
        context_lines=3,
        start_line=None,
        end_line=None,
        depth=None,
    )
    assert payload["data"]["resolved_commit"] == "a" * 40


def test_research_deep_writes_report(monkeypatch, tmp_path: Path) -> None:
    fetch = AsyncMock(
        return_value={
            "query": "q",
            "preset": "quick",
            "answer": "answer",
            "report_markdown": "# Report\n",
        }
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.deep_research.fetch_deep_research_payload",
        fetch,
    )
    output = tmp_path / "report.md"

    payload = _payload(
        runner.invoke(
            app,
            [
                "--quiet",
                "research",
                "deep",
                "--query",
                "q",
                "--depth",
                "quick",
                "--output",
                str(output),
            ],
        )
    )

    assert output.read_text(encoding="utf-8") == "# Report\n"
    assert payload["data"]["report_path"] == str(output)
    fetch.assert_awaited_once()


def test_content_fetch_accepts_jsonl_input(monkeypatch, tmp_path: Path) -> None:
    fetch = AsyncMock(return_value={"mode": "bulk", "results": [], "total_requested": 2})
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.content.fetch_payload",
        fetch,
    )
    source = tmp_path / "urls.jsonl"
    source.write_text(
        "https://one.example\n{\"id\": \"two\", \"url\": \"https://two.example\"}\n",
        encoding="utf-8",
    )

    _payload(
        runner.invoke(
            app,
            ["--quiet", "content", "fetch", "--input-file", str(source)],
        )
    )

    args = fetch.await_args
    assert args is not None
    assert args.kwargs["urls"] == [
        "https://one.example",
        "https://two.example",
    ]

def test_content_fetch_suggests_next_window(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.content.fetch_payload",
        AsyncMock(
            return_value={
                "mode": "single",
                "results": [
                    {
                        "url": "https://example.com",
                        "page_content": "part",
                        "window": {"has_more": True, "next_offset": 42},
                    }
                ],
            }
        ),
    )

    payload = _payload(
        runner.invoke(app, ["--quiet", "content", "fetch", "--url", "https://example.com"])
    )

    assert payload["suggested_next"] == [
        "uv run web-search-cli content fetch --url https://example.com --offset 42"
    ]
