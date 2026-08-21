from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from kindly_web_search_mcp_server.cli.app import app
from kindly_web_search_mcp_server.cli.services import jobs


def test_job_store_supports_idempotency_cancel_resume_and_completion(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "jobs.sqlite"
    monkeypatch.setenv("WEB_SEARCH_CLI_JOBS_DB", str(database))
    spawned: list[str] = []
    monkeypatch.setattr(jobs, "_spawn_worker", lambda job_id: spawned.append(job_id) or 1234)

    spec = {"query": "q", "research_goal": "goal", "output_dir": str(tmp_path / "out")}
    first = jobs.submit_job("research.collect", spec)
    duplicate = jobs.submit_job("research.collect", spec)

    assert first["job_id"] == duplicate["job_id"]
    assert first["status"] == "queued"
    assert spawned == [first["job_id"]]

    cancelled = jobs.cancel_job(first["job_id"])
    assert cancelled["status"] == "cancelled"
    resumed = jobs.resume_job(first["job_id"])
    assert resumed["status"] == "queued"
    assert len(spawned) == 2

    assert jobs.mark_running(first["job_id"], 5678) is True
    jobs.finish_job(first["job_id"], "succeeded", result={"report_path": "report.md"})
    completed = jobs.get_job(first["job_id"])
    assert completed["status"] == "succeeded"
    assert completed["result"]["report_path"] == "report.md"


def test_job_cancel_is_cooperative_for_running_job(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "jobs.sqlite"
    monkeypatch.setenv("WEB_SEARCH_CLI_JOBS_DB", str(database))
    monkeypatch.setattr(jobs, "_spawn_worker", lambda _job_id: 1234)

    created = jobs.submit_job("research.collect", {"query": "q"})
    assert jobs.mark_running(created["job_id"], 1234) is True
    requested = jobs.cancel_job(created["job_id"])

    assert requested["status"] == "running"
    assert requested["cancel_requested"] is True
    jobs.finish_job(created["job_id"], "cancelled", error="Cancellation requested.")
    assert jobs.get_job(created["job_id"])["status"] == "cancelled"


def test_worker_can_resume_a_failed_job_after_process_restart(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "jobs.sqlite"
    monkeypatch.setenv("WEB_SEARCH_CLI_JOBS_DB", str(database))
    monkeypatch.setattr(jobs, "_spawn_worker", lambda _job_id: 1234)

    created = jobs.submit_job("research.collect", {"query": "q"})
    jobs.finish_job(created["job_id"], "failed", error="simulated worker failure")
    assert jobs.get_job(created["job_id"])["status"] == "failed"

    resumed = jobs.resume_job(created["job_id"])
    assert resumed["status"] == "queued"

    from kindly_web_search_mcp_server.cli import job_worker

    async def _successful_collect(**_: object) -> dict[str, str]:
        return {"report_path": "report.md"}

    monkeypatch.setattr(job_worker, "collect_research_bundle", _successful_collect)
    assert job_worker.run(created["job_id"]) == 0
    completed = jobs.get_job(created["job_id"])
    assert completed["status"] == "succeeded"
    assert completed["result"]["report_path"] == "report.md"


def test_research_collect_no_wait_submits_local_job(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "jobs.sqlite"
    monkeypatch.setenv("WEB_SEARCH_CLI_JOBS_DB", str(database))
    monkeypatch.setattr(jobs, "_spawn_worker", lambda _job_id: 1234)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "--quiet",
            "research",
            "collect",
            "--query",
            "q",
            "--research-goal",
            "goal",
            "--output-dir",
            str(tmp_path / "bundle"),
            "--no-wait",
        ],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["data"]["status"] == "queued"
    assert payload["data"]["job_id"].startswith("job_")


def test_detached_worker_process_updates_job(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "jobs.sqlite"
    monkeypatch.setenv("WEB_SEARCH_CLI_JOBS_DB", str(database))

    created = jobs.submit_job("unsupported.kind", {})
    completed, timed_out = jobs.wait_for_job(
        created["job_id"],
        timeout_seconds=20.0,
        poll_interval_seconds=0.2,
    )

    assert timed_out is False
    assert completed["status"] == "failed"
    assert "Unsupported job specification" in completed["error"]
