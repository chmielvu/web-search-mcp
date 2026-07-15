from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from live_web_search_quality_support import dump_json, read_jsonl

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = Path(__file__).with_name("live_web_search_quality_queries.json")
DEFAULT_OUTPUT_ROOT = ROOT / "test-results" / "live-web-search"
FIXED_PARAMETERS = {"rewrite": True, "num_results": 15, "result_offset": 0}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def create_run_dir(output_root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root.resolve() / stamp
    suffix = 1
    while run_dir.exists():
        run_dir = output_root.resolve() / f"{stamp}-{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True)
    return run_dir


def server_environment(run_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        "LOG_LEVEL": "DEBUG",
        "FASTMCP_LOG_LEVEL": "WARNING",
        "ANALYTICS_DUCKDB_PATH": str(run_dir / "analytics.duckdb"),
        "PROCESS_LOGS_DUCKDB_PATH": str(run_dir / "process_logs.duckdb"),
        "QUERY_UNDERSTANDING_JSONL_PATH": str(run_dir / "query_understanding.jsonl"),
    }


def base_manifest(
    run_dir: Path,
    corpus_hash: str,
    args: argparse.Namespace,
    uv_executable: str,
) -> dict[str, Any]:
    return {
        "campaign_status": "running",
        "started_at": utc_now(),
        "ended_at": None,
        "run_dir": str(run_dir),
        "corpus_path": str(CORPUS_PATH),
        "corpus_sha256": corpus_hash,
        "attempted_ids": [],
        "attempted_count": 0,
        "completed_batches": [],
        "fixed_parameters": FIXED_PARAMETERS,
        "execution": {
            "batch_size": args.batch_size,
            "inter_batch_delay_seconds": args.inter_batch_delay_seconds,
            "call_timeout_seconds": args.call_timeout_seconds,
            "uv_executable": uv_executable,
            "python": platform.python_version(),
            "fastmcp": importlib.metadata.version("fastmcp"),
        },
        "observability": {
            "log_level": "DEBUG",
            "fastmcp_log_level": "WARNING",
            "analytics_db": str(run_dir / "analytics.duckdb"),
            "process_logs_db": str(run_dir / "process_logs.duckdb"),
            "server_stderr": str(run_dir / "server.stderr.log"),
        },
        "tool_schema": None,
        "analytics_gate": None,
        "abort_reason": None,
    }


def load_resume_manifest(
    run_dir: Path,
    corpus_hash: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("corpus_sha256") != corpus_hash:
        raise ValueError("resume corpus hash differs from the stored campaign")
    if manifest.get("fixed_parameters") != FIXED_PARAMETERS:
        raise ValueError("resume fixed parameters differ from the stored campaign")
    stored = manifest.get("execution") or {}
    requested = {
        "batch_size": args.batch_size,
        "inter_batch_delay_seconds": args.inter_batch_delay_seconds,
        "call_timeout_seconds": args.call_timeout_seconds,
    }
    if any(stored.get(key) != value for key, value in requested.items()):
        raise ValueError("resume execution parameters differ from the stored campaign")
    manifest.update(campaign_status="running", ended_at=None, abort_reason=None)
    return manifest


def refresh_manifest(run_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    calls = read_jsonl(run_dir / "calls.jsonl")
    ids = [record["id"] for record in calls]
    if len(ids) != len(set(ids)):
        raise ValueError("calls.jsonl contains duplicate query IDs")
    manifest["attempted_ids"] = ids
    manifest["attempted_count"] = len(ids)
    manifest["completed_batches"] = [
        batch
        for batch in range(1, 11)
        if sum(record.get("batch") == batch for record in calls) == 5
    ]
    dump_json(run_dir / "manifest.json", manifest)
    return calls


def validate_tool_schema(tools: list[Any]) -> dict[str, Any]:
    web_search = next((tool for tool in tools if tool.name == "web_search"), None)
    if web_search is None:
        raise RuntimeError("live server does not expose web_search")
    schema = web_search.model_dump(mode="json", by_alias=True)
    input_schema = schema.get("inputSchema") or schema.get("input_schema") or {}
    missing = {"query", "research_goal"} - set(input_schema.get("required") or [])
    if missing:
        raise RuntimeError(f"web_search schema lacks required fields: {sorted(missing)}")
    return schema
