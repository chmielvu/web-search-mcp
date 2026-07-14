from __future__ import annotations

import importlib.util
import shutil

import typer

from ..output import emit_json
from ..skill_paths import DEV_SKILL_PATH, REPO_ROOT, USER_SKILL_PATH


def _safe_find_spec(name: str) -> object:
    """find_spec that returns None instead of raising for missing parent packages."""
    try:
        return importlib.util.find_spec(name)
    except (ModuleNotFoundError, ValueError):
        return None


def register(app: typer.Typer) -> None:
    @app.command("doctor")
    def doctor_cmd() -> None:
        """Validate scaffold readiness without provider calls."""
        checks = [
            {"name": "package_importable", "ok": True},
            {"name": "typer_importable", "ok": _safe_find_spec("typer") is not None},
            {"name": "user_skill", "ok": USER_SKILL_PATH.exists(), "path": str(USER_SKILL_PATH)},
            {"name": "dev_skill", "ok": DEV_SKILL_PATH.exists(), "path": str(DEV_SKILL_PATH)},
            {"name": "duckdb_cli", "ok": shutil.which("duckdb") is not None, "required": False},
            {
                "name": "phoenix_instrumentor",
                "ok": _safe_find_spec("openinference.instrumentation.openai") is not None,
                "required": False,
            },
            {"name": "repo_root", "ok": REPO_ROOT.exists(), "path": str(REPO_ROOT)},
        ]
        emit_json({"checks": checks}, command="doctor")
