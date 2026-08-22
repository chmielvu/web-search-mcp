from __future__ import annotations

import logging
from unittest.mock import Mock
import json
from unittest.mock import AsyncMock
from typer.testing import CliRunner

from kindly_web_search_mcp_server.cli.app import app, main as cli_main
from kindly_web_search_mcp_server.cli.skill_paths import DEV_SKILL_PATH, USER_SKILL_PATH


runner = CliRunner()


def _payload(result) -> dict:
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def test_schema_lists_planned_commands() -> None:
    payload = _payload(runner.invoke(app, ["schema"]))
    schema = payload["data"]
    commands = {item["path"] for item in schema["commands"]}

    assert schema["command"] == "web-search-cli"
    assert schema["command_tree"]["path"] == "web-search-cli"
    assert "search web" in commands
    assert "links similar" in commands
    assert "analytics query" in commands
    assert "getskill" in commands
    assert any(item["path"] == "search web" and item["params"] for item in schema["commands"])


def test_reference_tools_covers_current_catalog() -> None:
    payload = _payload(runner.invoke(app, ["reference", "tools"]))
    tools = {item["tool"] for item in payload["data"]["tools"]}

    assert "fetch" in tools
    assert "get_content" not in tools
    assert "batch_get_content" not in tools
    assert "quick_web_search" in tools
    assert "composio_similarlinks" in tools
    assert "grok_search" in tools
    assert "code_search" in tools

def test_search_code_forwards_mcp_aligned_options(monkeypatch) -> None:
    mock_payload = AsyncMock(
        return_value={
            "query": "retry backoff",
            "outcome": "ok",
            "results": [],
            "repositories": [],
            "diagnostics": [],
            "stats": {},
            "query_metadata": {},
        }
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.search_code.fetch_code_search_payload",
        mock_payload,
    )

    payload = _payload(
        runner.invoke(
            app,
            [
                "search",
                "code",
                "--query",
                "retry backoff",
                "--research-goal",
                "find implementation examples",
                "--repository",
                "owner/repo",
                "--language",
                "Python",
                "--path",
                "src/",
                "--mode",
                "docs",
                "--deep",
            ],
        )
    )

    mock_payload.assert_awaited_once_with(
        "retry backoff",
        research_goal="find implementation examples",
        repositories=["owner/repo"],
        language="Python",
        path="src/",
        filename=None,
        extension=None,
        regexp=False,
        deep=True,
        repo_name=None,
        library_name=None,
        topic=None,
        mode="docs",
        huggingface_type="both",
        huggingface_sort_by="similarity",
        huggingface_hybrid=False,
        huggingface_min_likes=0,
        huggingface_min_downloads=0,
        huggingface_task=None,
        huggingface_license=None,
        huggingface_language=None,
        huggingface_modified_after=None,
        huggingface_min_param_count=0,
        huggingface_max_param_count=None,
    )
    assert payload["meta"]["command"] == "search code"
    assert payload["data"]["outcome"] == "ok"


def test_search_code_rejects_blank_query_before_adapter(monkeypatch) -> None:
    mock_payload = AsyncMock()
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.services.search_code.fetch_code_search_payload",
        mock_payload,
    )

    result = runner.invoke(app, ["search", "code", "--query", "   "])

    assert result.exit_code != 0
    mock_payload.assert_not_awaited()


def test_reference_tools_rejects_invalid_profile() -> None:
    result = runner.invoke(app, ["reference", "tools", "--profile", "bogus"])

    assert result.exit_code == 2


def test_reference_external_tools_lists_companion_clis() -> None:
    payload = _payload(runner.invoke(app, ["reference", "external-tools"]))
    commands = {item["command"] for item in payload["data"]["external_tools"]}

    assert "duckdb" in commands
    assert "wsl gcx" in commands
    assert "arize-phoenix" in commands


def test_global_profile_flows_into_json_meta() -> None:
    payload = _payload(runner.invoke(app, ["--profile", "research", "doctor"]))

    assert payload["meta"]["profile"] == "research"


def test_debug_enables_debug_logging(monkeypatch) -> None:
    configure_logging = Mock()
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.app.configure_logging",
        configure_logging,
    )

    payload = _payload(runner.invoke(app, ["--debug", "doctor"]))

    configure_logging.assert_called_once_with(level=logging.DEBUG, log_format="text")
    assert payload["meta"]["debug"] is True


def test_version_prints_project_version(capsys) -> None:
    cli_main(["--version"])
    assert capsys.readouterr().out.strip() == "0.1.8"


def test_brief_prints_one_paragraph(capsys) -> None:
    cli_main(["--brief"])
    brief = capsys.readouterr().out.strip()

    assert brief.startswith("The `web-search-cli` is the native, JSON-first command-line surface")
    assert "\n\n" not in brief


def test_root_help_emits_structured_json(capsys) -> None:
    cli_main(["--help"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["meta"]["command"] == "web-search-cli --help"
    assert payload["data"]["command"] == "web-search-cli"
    assert payload["data"]["brief"].startswith("The `web-search-cli` is the native")
    assert payload["data"]["skills"]


def test_subcommand_help_emits_structured_json(capsys) -> None:
    cli_main(["search", "web", "--help"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["meta"]["command"] == "search web --help"
    assert payload["data"]["command"] == "search web"
    assert any(param["name"] == "query" for param in payload["data"]["params"])


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
        "kindly_web_search_mcp_server.cli.services.search_web.fetch_web_search_payload",
        AsyncMock(
            return_value={
                "query": "web search query",
                "results": [],
                "total_results": 0,
                "providers_used": [],
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
                "--research-goal",
                "test goal",
            ],
        )
    )
    assert payload["meta"]["command"] == "search web"
    assert payload["data"]["query"] == "web search query"
    assert payload["data"]["total_results"] == 0
