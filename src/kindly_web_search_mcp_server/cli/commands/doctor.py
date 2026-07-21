from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import duckdb
import typer

from ...analytics.duckdb_store import _db_path
from ...analytics.views import ensure_views
from ...settings import settings
from ..output import emit_json
from ..skill_paths import DEV_SKILL_PATH, REPO_ROOT, USER_SKILL_PATH


def _safe_find_spec(name: str) -> object:
    """find_spec that returns None instead of raising for missing parent packages."""
    try:
        return importlib.util.find_spec(name)
    except (ModuleNotFoundError, ValueError):
        return None


def _check_duckdb_file(path: Path) -> dict:
    name = path.name
    if not path.exists():
        return {
            "name": f"duckdb_file_{name}",
            "ok": False,
            "path": str(path),
            "error": "file not found",
        }
    try:
        conn = duckdb.connect(str(path), read_only=True)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return {"name": f"duckdb_file_{name}", "ok": True, "path": str(path)}
    except Exception as exc:  # noqa: BLE001
        return {"name": f"duckdb_file_{name}", "ok": False, "path": str(path), "error": str(exc)}


def _check_analytics_schema() -> dict:
    path = _db_path()
    if not path.exists():
        return {"name": "analytics_schema", "ok": False, "error": "analytics database not found"}
    try:
        ensure_views(db_path=str(path))
        conn = duckdb.connect(str(path), read_only=True)
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        views = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.views WHERE table_schema = 'main'"
            ).fetchall()
        }
        conn.close()
        missing_tables = {"llm_call_log", "search_runs", "judge_evaluations"} - tables
        missing_views = {
            "vw_cost_attribution",
            "vw_end_to_end_quality",
            "vw_embedding_similarity",
        } - views
        ok = not missing_tables and not missing_views
        return {
            "name": "analytics_schema",
            "ok": ok,
            "missing_tables": sorted(missing_tables),
            "missing_views": sorted(missing_views),
        }
    except Exception as exc:  # noqa: BLE001
        return {"name": "analytics_schema", "ok": False, "error": str(exc)}


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
            {
                "name": "firecrawl_importable",
                "ok": _safe_find_spec("firecrawl") is not None,
                "required": False,
            },
            {"name": "repo_root", "ok": REPO_ROOT.exists(), "path": str(REPO_ROOT)},
            _check_duckdb_file(_db_path()),
            _check_duckdb_file(Path(settings.page_cache_duckdb_path)),
            _check_duckdb_file(Path(settings.transcript_cache_duckdb_path)),
            _check_analytics_schema(),
        ]
        emit_json({"checks": checks}, command="doctor")
