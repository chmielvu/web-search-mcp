from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..skill_paths import REPO_ROOT


TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "partial"})


def jobs_db_path() -> Path:
    configured = os.environ.get("WEB_SEARCH_CLI_JOBS_DB", "").strip()
    return Path(configured).expanduser() if configured else REPO_ROOT / "duckdb_data" / "cli" / "jobs.sqlite"


def jobs_log_dir() -> Path:
    return jobs_db_path().parent / "job-logs"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _connect() -> sqlite3.Connection:
    path = jobs_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            pid INTEGER,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_idempotency ON jobs(idempotency_key);
        """
    )
    return connection


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("spec_json", "result_json"):
        raw = result.get(key)
        if raw:
            try:
                result[key.removesuffix("_json")] = json.loads(raw)
            except json.JSONDecodeError:
                result[key.removesuffix("_json")] = raw
        result.pop(key, None)
    result["cancel_requested"] = bool(result.get("cancel_requested"))
    return result


def get_job(job_id: str) -> dict[str, Any]:
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError(f"Job '{job_id}' was not found.")
        return _decode(row)
    finally:
        connection.close()


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 100))
    connection = _connect()
    try:
        rows = connection.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (safe_limit,)
        ).fetchall()
        return [_decode(row) for row in rows]
    finally:
        connection.close()


def _idempotency_key(kind: str, spec: dict[str, Any]) -> str:
    encoded = json.dumps({"kind": kind, "spec": spec}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _spawn_worker(job_id: str) -> int:
    logs = jobs_log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / f"{job_id}.stdout.log"
    stderr_path = logs / f"{job_id}.stderr.log"
    stdout = stdout_path.open("a", encoding="utf-8")
    stderr = stderr_path.open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "cwd": str(REPO_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": stderr,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "kindly_web_search_mcp_server.cli.job_worker", job_id],
            **kwargs,
        )
    finally:
        stdout.close()
        stderr.close()
    return process.pid


def submit_job(
    kind: str,
    spec: dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    key = idempotency_key or _idempotency_key(kind, spec)
    connection = _connect()
    try:
        existing = connection.execute(
            """
            SELECT * FROM jobs
            WHERE idempotency_key = ? AND status IN ('queued', 'running', 'succeeded')
            ORDER BY created_at DESC LIMIT 1
            """,
            (key,),
        ).fetchone()
        if existing is not None:
            return _decode(existing)

        job_id = f"job_{uuid.uuid4().hex[:12]}"
        connection.execute(
            """
            INSERT INTO jobs (job_id, kind, status, spec_json, idempotency_key, created_at)
            VALUES (?, ?, 'queued', ?, ?, ?)
            """,
            (job_id, kind, json.dumps(spec, ensure_ascii=False), key, _now()),
        )
        connection.commit()
    finally:
        connection.close()

    try:
        pid = _spawn_worker(job_id)
    except Exception as exc:
        finish_job(job_id, "failed", error=f"Worker spawn failed: {type(exc).__name__}: {exc}")
        raise
    connection = _connect()
    try:
        connection.execute("UPDATE jobs SET pid = ? WHERE job_id = ?", (pid, job_id))
        connection.commit()
    finally:
        connection.close()
    return get_job(job_id)


def submit_research_collect_job(
    query: str,
    research_goal: str,
    *,
    output_dir: str,
    top_results: int,
    rewrite: bool,
    ai_summary: bool,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    return submit_job(
        "research.collect",
        {
            "query": query,
            "research_goal": research_goal,
            "output_dir": output_dir,
            "rewrite": rewrite,
            "ai_summary": ai_summary,
        },
        idempotency_key=idempotency_key,
    )


def mark_running(job_id: str, pid: int) -> bool:
    connection = _connect()
    try:
        cursor = connection.execute(
            """
            UPDATE jobs SET status = 'running', started_at = COALESCE(started_at, ?), pid = ?
            WHERE job_id = ? AND status = 'queued' AND cancel_requested = 0
            """,
            (_now(), pid, job_id),
        )
        connection.commit()
        return cursor.rowcount == 1
    finally:
        connection.close()


def is_cancel_requested(job_id: str) -> bool:
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT cancel_requested FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return bool(row and row[0])
    finally:
        connection.close()


def finish_job(
    job_id: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"Invalid terminal job status: {status}")
    connection = _connect()
    try:
        connection.execute(
            """
            UPDATE jobs
            SET status = ?, finished_at = ?, result_json = ?, error = ?
            WHERE job_id = ?
            """,
            (
                status,
                _now(),
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                error,
                job_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def cancel_job(job_id: str) -> dict[str, Any]:
    connection = _connect()
    try:
        row = connection.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError(f"Job '{job_id}' was not found.")
        status = row[0]
        if status == "queued":
            connection.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ?, cancel_requested = 1 WHERE job_id = ?",
                (_now(), job_id),
            )
        elif status not in TERMINAL_STATUSES:
            connection.execute(
                "UPDATE jobs SET cancel_requested = 1 WHERE job_id = ?", (job_id,)
            )
        connection.commit()
    finally:
        connection.close()
    return get_job(job_id)


def resume_job(job_id: str) -> dict[str, Any]:
    connection = _connect()
    try:
        row = connection.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError(f"Job '{job_id}' was not found.")
        if row[0] == "succeeded":
            return get_job(job_id)
        if row[0] not in {"failed", "cancelled", "partial"}:
            return get_job(job_id)
        connection.execute(
            """
            UPDATE jobs SET status = 'queued', started_at = NULL, finished_at = NULL,
                            pid = NULL, cancel_requested = 0, result_json = NULL, error = NULL
            WHERE job_id = ?
            """,
            (job_id,),
        )
        connection.commit()
    finally:
        connection.close()
    pid = _spawn_worker(job_id)
    connection = _connect()
    try:
        connection.execute("UPDATE jobs SET pid = ? WHERE job_id = ?", (pid, job_id))
        connection.commit()
    finally:
        connection.close()
    return get_job(job_id)


def wait_for_job(job_id: str, *, timeout_seconds: float, poll_interval_seconds: float) -> tuple[dict[str, Any], bool]:
    import time

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        job = get_job(job_id)
        if job["status"] in TERMINAL_STATUSES:
            return job, False
        if time.monotonic() >= deadline:
            return job, True
        time.sleep(max(0.1, poll_interval_seconds))
