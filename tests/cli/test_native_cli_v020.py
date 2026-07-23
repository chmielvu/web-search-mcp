from __future__ import annotations

import json
import logging

import pytest
from typer.testing import CliRunner

from kindly_web_search_mcp_server.cli.app import _needs_telemetry, app, main as cli_main
from kindly_web_search_mcp_server.cli.errors import match_hint_rule
from kindly_web_search_mcp_server.cli.exit_codes import ExitCode
from kindly_web_search_mcp_server.utils.logging import JsonStderrLogFormatter

runner = CliRunner()


def test_feedback_dry_run_prevents_file_creation_and_mutation(tmp_path, monkeypatch) -> None:
    feedback_dir = tmp_path / "feedback"
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.commands.feedback._FEEDBACK_DIR",
        feedback_dir,
    )

    # Dry-run create
    res = runner.invoke(
        app,
        ["--dry-run", "feedback", "create", "--message", "Dry run test", "--type", "bug"],
    )
    payload = _payload(res)
    assert payload["data"]["dry_run"] is True
    assert payload["meta"]["dry_run"] is True
    assert not (feedback_dir / "001.json").exists()

    # Real create
    runner.invoke(
        app,
        ["feedback", "create", "--message", "Real item", "--type", "bug"],
    )
    assert (feedback_dir / "001.json").exists()

    # Dry-run transition
    trans_res = runner.invoke(
        app,
        ["--dry-run", "feedback", "transition", "001", "--status", "closed"],
    )
    trans_payload = _payload(trans_res)
    assert trans_payload["data"]["dry_run"] is True
    assert trans_payload["data"]["would_transition"]["status"] == "closed"

    # Verify file on disk still open
    show_payload = _payload(runner.invoke(app, ["feedback", "show", "001"]))
    assert show_payload["data"]["feedback"]["status"] == "open"


def _payload(result) -> dict:
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def test_skills_command_lists_all_skills() -> None:
    payload = _payload(runner.invoke(app, ["skills"]))
    assert "skills" in payload["data"]
    skill_names = [s["name"] for s in payload["data"]["skills"]]
    assert "web-search-cli" in skill_names
    assert "getting-started" in skill_names


def test_skills_command_displays_skill_markdown(capsys) -> None:
    cli_main(["skills", "getting-started"])
    stdout = capsys.readouterr().out
    assert "# Getting Started with web-search-cli" in stdout


def test_feedback_create_list_transition_and_close(tmp_path, monkeypatch) -> None:
    feedback_dir = tmp_path / "feedback"
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.commands.feedback._FEEDBACK_DIR",
        feedback_dir,
    )

    # Create feedback
    res = runner.invoke(
        app,
        [
            "feedback",
            "create",
            "--message",
            "Test feedback message",
            "--type",
            "bug",
        ],
    )
    payload = _payload(res)
    assert payload["data"]["feedback"]["id"] == "001"
    assert payload["data"]["feedback"]["type"] == "bug"
    assert (feedback_dir / "001.json").exists()

    # List feedback
    list_payload = _payload(runner.invoke(app, ["feedback", "list"]))
    assert list_payload["data"]["total"] == 1
    assert list_payload["data"]["feedback_entries"][0]["message"] == "Test feedback message"

    # Show feedback
    show_payload = _payload(runner.invoke(app, ["feedback", "show", "001"]))
    assert show_payload["data"]["feedback"]["id"] == "001"

    # Transition feedback
    trans_payload = _payload(
        runner.invoke(app, ["feedback", "transition", "001", "--status", "in-progress"])
    )
    assert trans_payload["data"]["feedback"]["status"] == "in-progress"

    # Close feedback
    close_payload = _payload(runner.invoke(app, ["feedback", "close", "001"]))
    assert close_payload["data"]["feedback"]["status"] == "closed"


def test_response_includes_rules_skills_and_feedback() -> None:
    payload = _payload(runner.invoke(app, ["doctor"]))
    assert "rules" in payload
    assert any(r["name"] == "trigger" for r in payload["rules"])
    assert "content" in payload["rules"][0]  # Full content per v0.2.0 R1
    assert "skills" in payload
    assert "feedback" in payload
    assert "web-search-cli feedback create" in payload["feedback"]


def test_quiet_flag_suppresses_inline_context() -> None:
    payload = _payload(runner.invoke(app, ["--quiet", "doctor"]))
    assert "rules" not in payload
    assert "skills" not in payload
    assert "feedback" not in payload


def test_raw_mode_emits_bare_value() -> None:
    res = runner.invoke(app, ["--raw", "getskill"])
    assert res.exit_code == 0
    assert "# web-search-cli" in res.stdout


def test_fields_projection_filters_data() -> None:
    payload = _payload(runner.invoke(app, ["--fields", "checks", "doctor"]))
    assert "checks" in payload["data"]
    assert len(payload["data"]) == 1


def test_invalid_command_emits_structured_error_to_stderr(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["invalid-cmd-foo"])
    assert exc_info.value.code == int(ExitCode.USAGE_ERROR)
    err_text = capsys.readouterr().err.strip()
    err_json = json.loads(err_text)
    assert err_json["error"]["kind"] == "usage_error"
    assert "context" in err_json["error"]


def test_needs_telemetry_parses_options_and_commands() -> None:
    assert _needs_telemetry(["--profile", "research", "search", "web"]) is True
    assert _needs_telemetry(["--fields", "checks", "doctor"]) is False
    assert _needs_telemetry(["unknown-cmd"]) is False


def test_missing_feedback_exits_with_not_found_20(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    feedback_dir = tmp_path / "feedback"
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.cli.commands.feedback._FEEDBACK_DIR",
        feedback_dir,
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["feedback", "show", "999"])
    assert exc_info.value.code == 20
    err_text = capsys.readouterr().err.strip()
    err_json = json.loads(err_text)
    assert err_json["error"]["exit_code"] == 20
    assert err_json["error"]["kind"] == "not_found"


def test_exit_code_semantic_values() -> None:
    assert int(ExitCode.SUCCESS) == 0
    assert int(ExitCode.INTERNAL_ERROR) == 1
    assert int(ExitCode.USAGE_ERROR) == 2
    assert int(ExitCode.AUTH_ERROR) == 10
    assert int(ExitCode.PERMISSION_ERROR) == 11
    assert int(ExitCode.NOT_FOUND) == 20
    assert int(ExitCode.CONFLICT) == 30


def test_hint_rule_matching() -> None:
    rule_auth = match_hint_rule("401 Unauthorized access")
    assert rule_auth is not None
    assert rule_auth.kind == "auth_error"
    assert rule_auth.exit_code == ExitCode.AUTH_ERROR

    rule_404 = match_hint_rule("404 Not Found")
    assert rule_404 is not None
    assert rule_404.kind == "not_found"


def test_json_stderr_formatter() -> None:
    formatter = JsonStderrLogFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test log line",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test_logger"
    assert parsed["message"] == "Test log line"
    assert "timestamp" in parsed


def test_special_flags_help_quiet_and_profile(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["--profile=research", "--help"])
    out1 = json.loads(capsys.readouterr().out)
    assert out1["meta"]["profile"] == "research"
    assert "feedback" in out1

    cli_main(["--quiet", "--help"])
    out2 = json.loads(capsys.readouterr().out)
    assert "feedback" not in out2
    assert "rules" not in out2
    assert "skills" not in out2

    cli_main(["--help"])
    out3 = json.loads(capsys.readouterr().out)
    assert "feedback" in out3
    assert out3["meta"]["profile"] == "full"
