from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..metadata import cli_version
from ..runtime import get_runtime
from ..output import emit_json
from ..skill_paths import REPO_ROOT

feedback_app = typer.Typer(no_args_is_help=True)
_FEEDBACK_DIR = REPO_ROOT / "feedback"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _get_next_id() -> str:
    if not _FEEDBACK_DIR.exists():
        return "001"
    existing = []
    for p in _FEEDBACK_DIR.glob("*.json"):
        if p.stem.isdigit():
            existing.append(int(p.stem))
    next_num = max(existing, default=0) + 1
    return f"{next_num:03d}"


def _load_all_feedback() -> list[dict[str, Any]]:
    if not _FEEDBACK_DIR.exists():
        return []
    result = []
    for p in sorted(_FEEDBACK_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                result.append(data)
        except Exception:
            continue
    return result


def _save_feedback_file(entry: dict[str, Any]) -> Path:
    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    entry_id = str(entry["id"]).zfill(3)
    file_path = _FEEDBACK_DIR / f"{entry_id}.json"
    file_path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return file_path


@feedback_app.command("create")
def create_cmd(
    message: Annotated[
        str, typer.Option("--message", "-m", help="Feedback message or description.")
    ],
    feedback_type: Annotated[
        str,
        typer.Option(
            "--type", "-t", help="Feedback category: bug | requirement | suggestion | bad-output"
        ),
    ] = "bug",
    command_context: Annotated[
        str | None, typer.Option("--command", help="Failing command string for context.")
    ] = None,
    exit_code: Annotated[
        int, typer.Option("--exit-code", help="Exit code of failing command.")
    ] = 0,
) -> None:
    """Create a new feedback entry stored in feedback/{id}.json."""
    valid_types = {"bug", "requirement", "suggestion", "bad-output"}
    if feedback_type not in valid_types:
        raise CliError(
            kind="validation_error",
            message=f"Invalid feedback type '{feedback_type}'. Must be one of: {', '.join(sorted(valid_types))}",
            hint="Pass --type bug, requirement, suggestion, or bad-output.",
            exit_code=ExitCode.VALIDATION_ERROR,
            context={"feedback_type": feedback_type, "valid_types": sorted(valid_types)},
        )

    now = _utc_now()
    next_id = _get_next_id()
    entry = {
        "id": next_id,
        "type": feedback_type,
        "status": "open",
        "message": message,
        "context": {
            "version": cli_version(),
            "command": command_context or "web-search-cli",
            "exit_code": exit_code,
        },
        "created_at": now,
        "updated_at": now,
    }
    runtime = get_runtime()
    if runtime.dry_run:
        emit_json(
            {
                "dry_run": True,
                "would_create": entry,
                "path": f"feedback/{next_id}.json",
            },
            command="feedback create",
        )
        return
    saved_path = _save_feedback_file(entry)

    emit_json(
        {
            "feedback": entry,
            "path": str(saved_path.relative_to(REPO_ROOT)),
        },
        command="feedback create",
    )


@feedback_app.command("list")
def list_cmd(
    feedback_type: Annotated[
        str | None,
        typer.Option(
            "--type", "-t", help="Filter by category: bug | requirement | suggestion | bad-output"
        ),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option(
            "--status", "-s", help="Filter by status: open | in-progress | resolved | closed"
        ),
    ] = None,
) -> None:
    """List all recorded feedback entries in project feedback/ directory."""
    all_entries = _load_all_feedback()
    filtered = all_entries
    if feedback_type:
        filtered = [e for e in filtered if e.get("type") == feedback_type]
    if status:
        filtered = [e for e in filtered if e.get("status") == status]

    emit_json({"feedback_entries": filtered, "total": len(filtered)}, command="feedback list")


@feedback_app.command("show")
def show_cmd(
    feedback_id: Annotated[str, typer.Argument(help="Feedback ID to inspect (e.g. 001).")],
) -> None:
    """Show details for a specific feedback entry."""
    all_entries = _load_all_feedback()
    target_id = feedback_id.zfill(3)
    found = next((e for e in all_entries if str(e.get("id")).zfill(3) == target_id), None)
    if not found:
        raise CliError(
            kind="not_found",
            message=f"Feedback '{feedback_id}' not found in feedback/ directory.",
            hint="Run `web-search-cli feedback list` to view recorded feedback.",
            exit_code=ExitCode.NOT_FOUND,
            context={"feedback_id": feedback_id},
        )
    emit_json({"feedback": found}, command="feedback show")


@feedback_app.command("close")
def close_cmd(
    feedback_id: Annotated[str, typer.Argument(help="Feedback ID to close (e.g. 001).")],
) -> None:
    """Mark a feedback entry as closed."""
    all_entries = _load_all_feedback()
    target_id = feedback_id.zfill(3)
    found = next((e for e in all_entries if str(e.get("id")).zfill(3) == target_id), None)
    if not found:
        raise CliError(
            kind="not_found",
            message=f"Feedback '{feedback_id}' not found in feedback/ directory.",
            hint="Run `web-search-cli feedback list` to view recorded feedback.",
            exit_code=ExitCode.NOT_FOUND,
            context={"feedback_id": feedback_id},
        )
    runtime = get_runtime()
    if runtime.dry_run:
        emit_json(
            {
                "dry_run": True,
                "would_close": {**found, "status": "closed", "updated_at": _utc_now()},
            },
            command="feedback close",
        )
        return
    found["status"] = "closed"
    found["updated_at"] = _utc_now()
    _save_feedback_file(found)
    emit_json({"feedback": found}, command="feedback close")


@feedback_app.command("transition")
def transition_cmd(
    feedback_id: Annotated[str, typer.Argument(help="Feedback ID to update.")],
    status: Annotated[
        str,
        typer.Option("--status", "-s", help="New status: open | in-progress | resolved | closed"),
    ],
) -> None:
    """Transition feedback status."""
    valid_statuses = {"open", "in-progress", "resolved", "closed"}
    if status not in valid_statuses:
        raise CliError(
            kind="validation_error",
            message=f"Invalid status '{status}'. Must be one of: {', '.join(sorted(valid_statuses))}",
            hint="Pass --status open, in-progress, resolved, or closed.",
            exit_code=ExitCode.VALIDATION_ERROR,
            context={"status": status},
        )
    all_entries = _load_all_feedback()
    target_id = feedback_id.zfill(3)
    found = next((e for e in all_entries if str(e.get("id")).zfill(3) == target_id), None)
    if not found:
        raise CliError(
            kind="not_found",
            message=f"Feedback '{feedback_id}' not found in feedback/ directory.",
            hint="Run `web-search-cli feedback list` to view recorded feedback.",
            exit_code=ExitCode.NOT_FOUND,
            context={"feedback_id": feedback_id},
        )
    runtime = get_runtime()
    if runtime.dry_run:
        emit_json(
            {
                "dry_run": True,
                "would_transition": {**found, "status": status, "updated_at": _utc_now()},
            },
            command="feedback transition",
        )
        return
    found["status"] = status
    found["updated_at"] = _utc_now()
    _save_feedback_file(found)
    emit_json({"feedback": found}, command="feedback transition")


def register(app: typer.Typer) -> None:
    app.add_typer(feedback_app, name="feedback")
