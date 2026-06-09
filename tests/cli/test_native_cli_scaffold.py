from __future__ import annotations

import json
from unittest.mock import AsyncMock
from typer.testing import CliRunner

from kindly_web_search_mcp_server.cli.app import app
from kindly_web_search_mcp_server.cli.skill_paths import DEV_SKILL_PATH, USER_SKILL_PATH


runner = CliRunner()


def _payload(result) -> dict:
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def test_schema_lists_planned_commands() -> None:
    payload = _payload(runner.invoke(app, ["schema"]))
    commands = {item["path"] for item in payload["data"]["commands"]}

    assert "search web" in commands
    assert "links similar" in commands
    assert "images search" in commands
    assert "getskill" in commands


def test_reference_tools_covers_current_catalog() -> None:
    payload = _payload(runner.invoke(app, ["reference", "tools"]))
    tools = {item["tool"] for item in payload["data"]["tools"]}

    assert len(tools) == 16
    assert "quick_web_search" in tools
    assert "composio_similarlinks" in tools
    assert "composio_image_search" in tools


def test_reference_tools_rejects_invalid_profile() -> None:
    result = runner.invoke(app, ["reference", "tools", "--profile", "bogus"])

    assert result.exit_code == 2


def test_reference_external_tools_lists_companion_clis() -> None:
    payload = _payload(runner.invoke(app, ["reference", "external-tools"]))
    commands = {item["command"] for item in payload["data"]["external_tools"]}

    assert "duckdb" in commands
    assert "wsl gcx" in commands
    assert "langfuse" in commands


def test_getskill_prints_user_skill_verbatim() -> None:
    result = runner.invoke(app, ["getskill"])

    assert result.exit_code == 0
    assert result.stdout == USER_SKILL_PATH.read_text(encoding="utf-8")


def test_getskill_dev_prints_dev_skill_verbatim() -> None:
    result = runner.invoke(app, ["getskill", "--dev"])

    assert result.exit_code == 0
    assert result.stdout == DEV_SKILL_PATH.read_text(encoding="utf-8")


def test_search_web_can_be_injected_with_stubbed_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.commands.search.fetch_web_search_payload",
        AsyncMock(
            return_value={
                "query": "web search query",
                "results": [],
                "total_results": 0,
                "providers_used": [],
                "result_window": {
                    "offset": 0,
                    "returned": 0,
                    "candidate_count": 0,
                    "has_more": False,
                    "next_offset": None,
                },
            }
        ),
    )

    payload = _payload(
        runner.invoke(
            app,
            [
                "search",
                "web",
                "--query",
                "web search query",
                "--num-results",
                "3",
            ],
        )
    )

    assert payload["meta"]["command"] == "search web"
    assert payload["data"]["query"] == "web search query"
    assert payload["data"]["total_results"] == 0
