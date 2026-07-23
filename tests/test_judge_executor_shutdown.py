"""BUG3: judge executor lazy singleton + shutdown + parallel facet count."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import pytest

from kindly_web_search_mcp_server.analytics import judges
from kindly_web_search_mcp_server.analytics.writers.schema import ensure_store_schema
from kindly_web_search_mcp_server import settings as settings_module


@pytest.fixture
def temp_db(monkeypatch):
    tmp_dir = tempfile.mkdtemp(prefix="judge_shutdown_")
    db_path = str(Path(tmp_dir) / "judge.duckdb")
    monkeypatch.setattr(settings_module.settings, "analytics_duckdb_path", db_path)
    monkeypatch.setattr(settings_module.settings, "flockmtl_enabled", True)
    ensure_store_schema(db_path=db_path)
    yield db_path
    Path(db_path).unlink(missing_ok=True)
    Path(tmp_dir).rmdir()


def _seed_run(db_path: str, run_key: str, *, final_count: int = 3) -> None:
    con = duckdb.connect(db_path)
    try:
        con.execute(
            """
            INSERT INTO search_runs (
                run_key, query, research_goal, intent, status,
                selected_providers, final_result_count, rewrite_enabled,
                rewritten_branch_queries
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_key,
                "how to test judges",
                "verify parallel facets",
                "coding",
                "success",
                ["brave"],
                final_count,
                False,
                [],
            ],
        )
        for i in range(1, final_count + 1):
            con.execute(
                """
                INSERT INTO final_results (run_key, rank, title, link, snippet)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    run_key,
                    i,
                    f"Title {i}",
                    f"https://example.com/{i}",
                    f"snippet {i}",
                ],
            )
        con.execute(
            """
            INSERT INTO rerank_stages (run_key, stage, input_count, output_count, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            [run_key, "cross_encoder", 10, 5, "success"],
        )
        for i in range(1, 4):
            con.execute(
                """
                INSERT INTO rerank_candidates (
                    run_key, stage, link, rank_before, rank_after, survived
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    run_key,
                    "cross_encoder",
                    f"https://example.com/{i}",
                    i,
                    i,
                    True,
                ],
            )
    finally:
        con.close()


def _fixed_prompt_response(prompt_name: str) -> str:
    payloads = {
        "judge_run_overview": {
            "verdict": "good",
            "analysis": "ok",
            "recommendations": [],
            "confidence": 3,
        },
        "judge_intent_coherence": {
            "verdict": "coherent",
            "confidence": 3,
            "reasoning": "matches",
        },
        "judge_rewrite_coverage": {
            "covered_count": 2,
            "redundant": False,
            "missing_facets": [],
            "confidence": 3,
            "reasoning": "fine",
        },
        "judge_rerank_improvement": {
            "verdict": "improved",
            "confidence": 3,
            "reasoning": "better order",
        },
        "judge_result_quality": {
            "intent_match": True,
            "informativeness": 3,
            "confidence": 3,
            "reasoning": "relevant",
        },
        "judge_failure_cause": {
            "root_cause": "other",
            "stage": "retrieve",
            "suggested_fix": "n/a",
            "confidence": 2,
            "reasoning": "n/a",
        },
    }
    body = payloads.get(prompt_name, {"verdict": "ok", "confidence": 1, "reasoning": "x"})
    return f"[RESULT]\n{json.dumps(body)}"


def test_shutdown_judge_executor_does_not_raise() -> None:
    judges.shutdown_judge_executor(wait=False)
    judges.shutdown_judge_executor(wait=False)  # idempotent
    # Reschedule still works after shutdown (lazy recreate).
    fut = judges.schedule_judge_search_run("nonexistent-run")
    assert fut is not None
    judges.shutdown_judge_executor(wait=False)


def test_parallel_facets_write_expected_count(temp_db: str, monkeypatch) -> None:
    run_key = "rk-parallel-1"
    _seed_run(temp_db, run_key, final_count=3)

    def fake_run_prompt(
        connection: Any,
        *,
        model_name: str,
        prompt_name: str,
        context_columns: list[dict[str, object]],
        response_format: dict[str, object] | None = None,
    ) -> tuple[str | None, float]:
        return _fixed_prompt_response(prompt_name), 0.01

    monkeypatch.setattr(judges, "_run_prompt", fake_run_prompt)
    monkeypatch.setattr(judges, "_ensure_loaded", lambda _c: True)

    written = judges.judge_search_run(run_key, db_path=temp_db)
    # overview + intent + 1 rerank stage + 3 result_quality = 6
    assert written == 6

    con = duckdb.connect(temp_db, read_only=True)
    try:
        kinds = {
            row[0]
            for row in con.execute(
                "SELECT judgment_kind FROM llm_judgments WHERE run_key = ?",
                [run_key],
            ).fetchall()
        }
        assert "run_overview" in kinds
        assert "intent_coherence" in kinds
        assert "rerank_improvement" in kinds
        assert "result_quality" in kinds
        count_rq = con.execute(
            "SELECT count(*) FROM llm_judgments "
            "WHERE run_key = ? AND judgment_kind = 'result_quality'",
            [run_key],
        ).fetchone()[0]
        assert count_rq == 3
        # All rows keyed correctly (no empty run_key from parallel path).
        empty = con.execute("SELECT count(*) FROM llm_judgments WHERE run_key = ''").fetchone()[0]
        assert empty == 0
    finally:
        con.close()


def test_daemon_judge_pool_not_in_atexit_registry() -> None:
    """Workers must not register in concurrent.futures' atexit join map."""
    from concurrent.futures.thread import _threads_queues  # type: ignore[attr-defined]

    judges.shutdown_judge_executor(wait=False)
    before = set(_threads_queues.keys())
    try:
        ex = judges._get_judge_executor()
        fut = ex.submit(lambda: None)
        fut.result(timeout=2)
        after = set(_threads_queues.keys())
        assert after == before, "judge workers must not join _threads_queues"
    finally:
        judges.shutdown_judge_executor(wait=False)


def test_cli_exit_not_pinned_by_slow_judge_subprocess() -> None:
    """Process must exit quickly after shutdown_judge_executor(wait=False)
    even when a long-running judge job is still in flight.

    Cold import of analytics.judges is multi-second on this tree; bound is
    relative to a 30s abandoned sleep, not absolute wall-clock for import.
    """
    import subprocess
    import sys
    import textwrap
    import time

    script = textwrap.dedent(
        """
        import time
        import threading
        t0 = time.monotonic()
        from kindly_web_search_mcp_server.analytics import judges
        t_import = time.monotonic()

        started = threading.Event()

        def slow():
            started.set()
            time.sleep(30)

        ex = judges._get_judge_executor()

        def outer():
            pool = judges._DaemonThreadPoolExecutor(
                max_workers=2, thread_name_prefix="judge-facet-test"
            )
            try:
                futs = [pool.submit(slow) for _ in range(2)]
                for f in futs:
                    f.result()
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

        ex.submit(outer)
        # Prove at least one slow facet worker is running before we abandon.
        assert started.wait(timeout=5), "slow facet worker never started"
        judges.shutdown_judge_executor(wait=False)
        print(f"import_s={t_import - t0:.3f}", flush=True)
        print(f"post_shutdown_s={time.monotonic() - t_import:.3f}", flush=True)
        """
    )
    started = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Import alone can be ~7s; 30s abandoned sleep must not pin exit.
        stdout, stderr = proc.communicate(timeout=25)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise AssertionError(
            f"child still running after 25s (pinned by judge threads?)\n"
            f"stdout={stdout!r}\nstderr={stderr!r}"
        ) from None
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, f"rc={proc.returncode}\nstderr={stderr}\nstdout={stdout}"
    assert "post_shutdown_s=" in stdout, stdout
    # Total wall must be far below the 30s abandoned sleep.
    assert elapsed < 20.0, (
        f"process exit took {elapsed:.2f}s (expected << 30s sleep)\nstdout={stdout}"
    )
