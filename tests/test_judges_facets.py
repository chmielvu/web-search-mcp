"""Unit tests for the six facet-decomposed judgments in `analytics/judges.py`.

All LLM calls are mocked at the `_run_prompt` boundary -- no Mistral
calls, no FlockMTL extension load required. `_ensure_loaded` is also
patched to skip FlockMTL installation (the test fixture owns the
schema bootstrap directly).

Each test seeds a `search_runs` row + child rows, calls
`judges.judge_search_run(run_key, db_path=...)`, and asserts the
expected `llm_judgments` row(s) come out with the right shape.

Judge-blindness contracts (`judge_result_quality` and
`judge_run_overview` must NOT receive reranker scores in their LLM
context) are enforced here at the data-plane level by inspecting the
`context_columns` captured by the mocked `_run_prompt`.

Plain pytest functions (NOT unittest.TestCase) so fixtures inject by
positional argument -- matches the repo convention in
test_judge_after_outcome_write.py.
"""

from __future__ import annotations

import inspect
import json
import tempfile
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import duckdb
import pytest

from kindly_web_search_mcp_server.analytics import judges
from kindly_web_search_mcp_server.analytics.writers.schema import ensure_store_schema
from kindly_web_search_mcp_server import settings


# --------------------------------------------------------------------------
# Fixtures + helpers
# --------------------------------------------------------------------------


@pytest.fixture
def temp_db(monkeypatch):
    """Provide a temp analytics DuckDB path + bootstrap schema. Returns the path string."""
    tmp_dir = tempfile.mkdtemp(prefix="judge_facets_")
    db_path = str(Path(tmp_dir) / "facets.duckdb")
    monkeypatch.setattr(settings.settings, "analytics_duckdb_path", db_path)
    ensure_store_schema(db_path=db_path)
    yield db_path
    Path(db_path).unlink(missing_ok=True)
    Path(tmp_dir).rmdir()


def _seed_run(
    db_path: str,
    *,
    run_key: str,
    status: str = "success",
    rewrite_enabled: bool = True,
    rewrites: list[str] | None = None,
    final_count: int = 3,
    intent: str = "coding",
    providers: list[str] | None = None,
    branches: list[tuple[str, int, str | None]] | None = None,
    rerank_stages: list[tuple[str, int, int]] | None = None,
    final_results: list[tuple[int, str, str, str]] | None = None,
) -> None:
    """Insert one synthetic search_run + optional child rows.

    Args:
        branches: list of (branch_role, results_count, branch_query_or_None)
        rerank_stages: list of (stage, input_count, output_count)
        final_results: list of (rank, title, link, snippet)
    """
    rewrites_json = json.dumps(rewrites or [])
    providers = providers or ["brave"]
    con = duckdb.connect(db_path)
    try:
        con.execute(
            """
            INSERT INTO search_runs (
              run_key, query, intent, research_goal, understanding_confidence,
              rewritten_branch_queries, selected_providers, branch_count,
              merged_count, reranked_count, final_result_count, candidate_count,
              rewrite_enabled, status, error_type, tool_call_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_key,
                "python async library",
                intent,
                "best lib",
                0.9,
                rewrites_json,  # JSON [] or 4 variants
                providers,  # VARCHAR[]
                len(branches or []),
                0,
                0,
                final_count,
                0,
                rewrite_enabled,
                status,
                "" if status == "success" else "TimeoutError",
                "tc1",  # tool_call_id (nullable but populated)
            ],
        )
        # search_branches (branch_query NOT NULL). We inject `branch_query` as the
        # third tuple element so the test author controls it explicitly.
        for idx, item in enumerate(branches or []):
            if len(item) == 3:
                role, rc, bquery = item
            else:  # legacy 3-tuple (role, rc, error_type) for the failure_cause path
                role, rc, _err = item
                bquery = f"query_{idx}"
            con.execute(
                "INSERT INTO search_branches "
                "(run_key, branch_index, branch_role, branch_query, results_count) "
                "VALUES (?, ?, ?, ?, ?)",
                [run_key, idx, role, bquery, rc],
            )
            # Each branch needs at least one provider_calls row (canonical
            # per-branch error signal lives there, joined in judges).
            con.execute(
                "INSERT INTO provider_calls "
                "(run_key, branch_index, provider, status) "
                "VALUES (?, ?, ?, 'success')",
                [run_key, idx, "brave"],
            )
        for stage, inp, outp in rerank_stages or []:
            con.execute(
                "INSERT INTO rerank_stages "
                "(run_key, stage, provider, model, input_count, output_count, status, score_threshold) "
                "VALUES (?, ?, 'ce', 'ce-v1', ?, ?, 'success', 0.5)",
                [run_key, stage, inp, outp],
            )
            con.execute(
                "INSERT INTO rerank_candidates "
                "(run_key, stage, link, rank_before, rank_after, survived, llm_raw_score, fused_score) "
                "VALUES (?, ?, ?, 0, 1, true, 0.9, 0.8)",
                [run_key, stage, f"https://{stage}.example.com"],
            )
        for rank, title, link, snippet in final_results or []:
            con.execute(
                "INSERT INTO final_results "
                "(run_key, rank, title, link, snippet, final_score, providers) "
                "VALUES (?, ?, ?, ?, ?, 0.9, ?)",
                [run_key, rank, title, link, snippet, providers],
            )
    finally:
        con.close()


def _judgment_rows(db_path: str, run_key: str) -> list[tuple]:
    """Return all llm_judgments rows for run_key, ordered by recorded_at."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        return con.execute(
            "SELECT facet, judgment_kind, judgment_target, model_name, "
            "verdict, status, rubric_version, confidence, "
            "COALESCE(reasoning, '') AS reasoning, "
            "COALESCE(context_shown, '') AS context_shown, duration_ms "
            "FROM llm_judgments WHERE run_key = ? ORDER BY recorded_at",
            [run_key],
        ).fetchall()
    finally:
        con.close()


def _canned_per_facet() -> dict[str, str]:
    """Return prompt_name -> raw_with[RESULT] for the mocked _run_prompt."""
    return {
        "judge_run_overview": (
            "Feedback: solid run. "
            + "[RESULT] "
            + json.dumps(
                {
                    "verdict": "good",
                    "analysis": "covers topic",
                    "recommendations": ["add neural branch"],
                    "confidence": 4,
                }
            )
        ),
        "judge_intent_coherence": (
            "Feedback: coherent. "
            + "[RESULT] "
            + json.dumps({"verdict": "coherent", "confidence": 3, "reasoning": "matches goal"})
        ),
        "judge_rewrite_coverage": (
            "Feedback: 4 distinct. "
            + "[RESULT] "
            + json.dumps(
                {
                    "covered_count": 4,
                    "redundant": False,
                    "missing_facets": [],
                    "confidence": 4,
                    "reasoning": "good spread",
                }
            )
        ),
        "judge_rerank_improvement": (
            "Feedback: improved. "
            + "[RESULT] "
            + json.dumps({"verdict": "improved", "confidence": 3, "reasoning": "good reorder"})
        ),
        "judge_result_quality": (
            "Feedback: match. "
            + "[RESULT] "
            + json.dumps(
                {
                    "intent_match": True,
                    "informativeness": 4,
                    "confidence": 4,
                    "reasoning": "direct",
                }
            )
        ),
        "judge_failure_cause": (
            "Feedback: provider timeout. "
            + "[RESULT] "
            + json.dumps(
                {
                    "root_cause": "provider_timeout",
                    "stage": "retrieval",
                    "suggested_fix": "fallback",
                    "confidence": 4,
                    "reasoning": "all branches timed out",
                }
            )
        ),
    }


@pytest.fixture
def mock_run_prompt(monkeypatch):
    """Patch `judges._run_prompt` to return canned per-facet responses; record every call."""
    canned = _canned_per_facet()
    calls: list[tuple[str, str, list[dict[str, Any]]]] = []

    def _fake(connection, *, model_name, prompt_name, context_columns):
        calls.append((prompt_name, model_name, list(context_columns)))
        raw = canned.get(prompt_name)
        if raw is None:
            raw = 'Feedback: ? [RESULT] {"verdict":"unknown","confidence":2,"reasoning":"?"}'
        return (raw, 0.01)

    monkeypatch.setattr(judges, "_run_prompt", _fake)
    return calls


@pytest.fixture
def mock_ensure_loaded(monkeypatch):
    """Skip FlockMTL install/load -- pretend it succeeded."""
    monkeypatch.setattr(judges, "_ensure_loaded", lambda connection: True)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestJudgeFacets:
    """Six facet-decomposed judgment tests."""

    def test_run_overview_fires_first_per_run(
        self, temp_db, mock_run_prompt, mock_ensure_loaded
    ) -> None:
        """judge_run_overview fires FIRST; one row; verdict in good/mixed/bad; digest is reranker-blind."""
        from kindly_web_search_mcp_server.analytics.judges import judge_search_run

        _seed_run(
            temp_db,
            run_key="rk1",
            rewrite_enabled=True,
            rewrites=["v1", "v2", "v3", "v4"],
            final_count=1,
            branches=[("exact", 5, "exact python async")],
            rerank_stages=[("cross_encoder", 5, 3)],
            final_results=[(1, "Title", "https://a.example.com", "snippet")],
        )
        judge_search_run("rk1", db_path=temp_db)

        prompt_call_order = [c[0] for c in mock_run_prompt]
        assert prompt_call_order[0] == "judge_run_overview", (
            f"overview must fire first; order was {prompt_call_order}"
        )
        rows = _judgment_rows(temp_db, "rk1")
        overview = [r for r in rows if r[0] == "run_overview"]
        assert len(overview) == 1, f"expected exactly 1 overview row, got {len(overview)}"
        (facet, kind, target, model, verdict, status, rv, conf, reasoning, ctx, dur) = overview[0]
        assert facet == "run_overview"
        assert kind == "run_overview"
        assert target == "rk1"
        assert model == "judge_quality"
        assert verdict == "good"
        assert status == "success"
        assert rv == "v1"
        assert conf == 4
        assert "covers topic" in reasoning
        assert "Recommendations:" in reasoning
        # Blindness at the data plane: the overview's only context column is `run_digest`,
        # and the digest must NOT carry any banned score name.
        overview_call = next(c for c in mock_run_prompt if c[0] == "judge_run_overview")
        ctx_cols = [col["name"] for col in overview_call[2]]
        assert ctx_cols == ["run_digest"], ctx_cols
        digest_text = overview_call[2][0]["data"]
        for banned in judges._BANNED_RERANK_SCORES:
            assert banned not in digest_text, f"BLINDNESS VIOLATED: digest leaks {banned}"

    def test_intent_coherence_fires_once(
        self, temp_db, mock_run_prompt, mock_ensure_loaded
    ) -> None:
        """judge_intent_coherence fires exactly once per run with rubric_version=v1."""
        from kindly_web_search_mcp_server.analytics.judges import judge_search_run

        _seed_run(temp_db, run_key="rk1", final_count=0)
        judge_search_run("rk1", db_path=temp_db)
        rows = _judgment_rows(temp_db, "rk1")
        intent_rows = [r for r in rows if r[0] == "intent_coherence"]
        assert len(intent_rows) == 1
        (facet, kind, target, model, verdict, status, rv, conf, reasoning, ctx, dur) = intent_rows[
            0
        ]
        assert kind == "intent_coherence"
        assert target == "rk1"
        assert rv == "v1"
        assert verdict == "coherent"
        assert conf == 3
        assert reasoning, "reasoning must be non-empty"

    def test_rewrite_coverage_fires_iff_rewrites_enabled(
        self, temp_db, mock_run_prompt, mock_ensure_loaded
    ) -> None:
        """judge_rewrite_coverage fires iff rewrite_enabled AND rewrites non-empty."""
        from kindly_web_search_mcp_server.analytics.judges import judge_search_run

        # Case A: disabled AND no rewrites -> 0 rows
        _seed_run(temp_db, run_key="rk_disabled", rewrite_enabled=False, rewrites=[])
        judge_search_run("rk_disabled", db_path=temp_db)
        coverage_a = [
            r for r in _judgment_rows(temp_db, "rk_disabled") if r[0] == "rewrite_coverage"
        ]
        assert coverage_a == [], "disabled + empty rewrites should yield 0 rows"

        # Case B: enabled with 4 rewrites -> 1 row, compact verdict "covered=N/4; ..."
        _seed_run(
            temp_db,
            run_key="rk_enabled",
            rewrite_enabled=True,
            rewrites=["v1", "v2", "v3", "v4"],
            final_count=0,
        )
        judge_search_run("rk_enabled", db_path=temp_db)
        coverage_b = [
            r for r in _judgment_rows(temp_db, "rk_enabled") if r[0] == "rewrite_coverage"
        ]
        assert len(coverage_b) == 1
        assert "covered=" in coverage_b[0][4], (
            f"compact verdict should be 'covered=N/4; ...' shape, got {coverage_b[0][4]!r}"
        )

    def test_rerank_improvement_fires_per_stage(
        self, temp_db, mock_run_prompt, mock_ensure_loaded
    ) -> None:
        """judge_rerank_improvement fires once per rerank_stages row; context is positional only."""
        from kindly_web_search_mcp_server.analytics.judges import judge_search_run

        _seed_run(
            temp_db,
            run_key="rk1",
            final_count=1,
            rerank_stages=[("cross_encoder", 10, 8), ("fusion", 8, 6)],
            final_results=[(1, "Title", "https://a.example.com", "snippet")],
        )
        judge_search_run("rk1", db_path=temp_db)
        rows = _judgment_rows(temp_db, "rk1")
        rerank_rows = [r for r in rows if r[0] == "rerank_improvement"]
        assert len(rerank_rows) == 2
        targets = {r[2] for r in rerank_rows}
        assert targets == {"cross_encoder", "fusion"}, targets
        rerank_calls = [c for c in mock_run_prompt if c[0] == "judge_rerank_improvement"]
        assert len(rerank_calls) == 2
        ctx_names = {col["name"] for col in rerank_calls[0][2]}
        assert ctx_names == {"query", "stage", "before", "after"}, ctx_names

    def test_result_quality_fires_per_final_result_with_blindness(
        self, temp_db, mock_run_prompt, mock_ensure_loaded
    ) -> None:
        """judge_result_quality fires per final_results row; context MUST be reranker-blind."""
        from kindly_web_search_mcp_server.analytics.judges import judge_search_run

        _seed_run(
            temp_db,
            run_key="rk1",
            final_count=3,
            final_results=[
                (1, "Title1", "https://a.example.com", "snippet1"),
                (2, "Title2", "https://b.example.com", "snippet2"),
                (3, "Title3", "https://c.example.com", "snippet3"),
            ],
        )
        judge_search_run("rk1", db_path=temp_db)
        rows = _judgment_rows(temp_db, "rk1")
        rq_rows = [r for r in rows if r[0] == "result_quality"]
        assert len(rq_rows) == 3, f"expected 3 result_quality rows, got {len(rq_rows)}"
        targets = sorted(r[2] for r in rq_rows)
        assert targets == sorted(
            ["https://a.example.com", "https://b.example.com", "https://c.example.com"]
        ), targets
        # Blindness: the result-quality context MUST NOT include any banned score.
        rq_calls = [c for c in mock_run_prompt if c[0] == "judge_result_quality"]
        assert len(rq_calls) == 3
        ctx_names = {col["name"] for col in rq_calls[0][2]}
        assert ctx_names == {
            "query",
            "research_goal",
            "intent",
            "rank",
            "title",
            "snippet",
        }, f"result_quality context drifted from whitelist: {ctx_names}"
        for c in rq_calls:
            for col in c[2]:
                assert "score" not in str(col["data"]).lower(), (
                    f"BLINDNESS VIOLATED: context column {col['name']} contains score"
                )

    def test_failure_cause_only_for_failed_or_empty_runs(
        self, temp_db, mock_run_prompt, mock_ensure_loaded
    ) -> None:
        """judge_failure_cause fires iff status != 'success' OR final_count == 0."""
        from kindly_web_search_mcp_server.analytics.judges import judge_search_run

        # Case A: success + non-empty -> 0 rows
        _seed_run(
            temp_db,
            run_key="rk_success",
            status="success",
            final_count=2,
            final_results=[
                (1, "T", "https://a.example.com", "s"),
                (2, "T2", "https://b.example.com", "s2"),
            ],
        )
        judge_search_run("rk_success", db_path=temp_db)
        success_rows = [r for r in _judgment_rows(temp_db, "rk_success") if r[0] == "failure_cause"]
        assert success_rows == [], (
            f"success+non-empty should yield 0 failure_cause rows, got {success_rows}"
        )

        # Case B: success + zero results -> 1 row
        _seed_run(temp_db, run_key="rk_empty", status="success", final_count=0)
        judge_search_run("rk_empty", db_path=temp_db)
        empty_rows = [r for r in _judgment_rows(temp_db, "rk_empty") if r[0] == "failure_cause"]
        assert len(empty_rows) == 1
        assert "@" in empty_rows[0][4], (
            f"compact verdict should be '<root> @ <stage>' shape, got {empty_rows[0][4]!r}"
        )

        # Case C: status=error -> 1 row
        _seed_run(temp_db, run_key="rk_err", status="error", final_count=0)
        judge_search_run("rk_err", db_path=temp_db)
        err_rows = [r for r in _judgment_rows(temp_db, "rk_err") if r[0] == "failure_cause"]
        assert len(err_rows) == 1


class TestParseResultAndErrorPath:
    """Parser unit tests + parse-failure storage path."""

    def test_parse_extracts_result_json(self) -> None:
        from kindly_web_search_mcp_server.analytics.judges import _parse_result

        raw = 'Feedback: ok. [RESULT] {"verdict":"good","confidence":3,"reasoning":"x"}'
        parsed = _parse_result(raw)
        assert parsed["verdict"] == "good"
        assert parsed["confidence"] == 3

    def test_parse_returns_none_for_missing_marker(self) -> None:
        from kindly_web_search_mcp_server.analytics.judges import _parse_result

        assert _parse_result("no marker here") is None
        assert _parse_result(None) is None
        assert _parse_result("") is None

    def test_parse_returns_none_for_malformed_json(self) -> None:
        from kindly_web_search_mcp_server.analytics.judges import _parse_result

        assert _parse_result("[RESULT] this is not json") is None

    def test_parse_recovers_first_json_block(self) -> None:
        from kindly_web_search_mcp_server.analytics.judges import _parse_result

        raw = '[RESULT] {"a":1} trailing commentary'
        assert _parse_result(raw) == {"a": 1}

    def test_parse_failure_stored_as_error_row_no_crash(
        self, temp_db, mock_ensure_loaded, monkeypatch
    ) -> None:
        """Model that omits [RESULT] stores status='error' without crashing the orchestrator."""
        from kindly_web_search_mcp_server.analytics.judges import judge_search_run

        def _broken_prompt(connection, *, model_name, prompt_name, context_columns):
            return ("Feedback: oops, no RESULT token here.", 0.01)

        monkeypatch.setattr(judges, "_run_prompt", _broken_prompt)
        _seed_run(temp_db, run_key="rk_broken", final_count=0)
        n = judge_search_run("rk_broken", db_path=temp_db)  # must not raise
        assert n > 0
        rows = _judgment_rows(temp_db, "rk_broken")
        statuses = [r[5] for r in rows]
        assert all(s == "error" for s in statuses), (
            f"expected every row status='error' on parse failure, got {statuses}"
        )
        facets_written = {r[0] for r in rows}
        assert "run_overview" in facets_written
        assert "intent_coherence" in facets_written
        assert "failure_cause" in facets_written


class TestScheduleSignaturePreserved:
    """schedule_judge_search_run contract preserved (single-positional-arg + returns Future)."""

    def test_schedule_signature_unchanged(self) -> None:
        sig = inspect.signature(judges.schedule_judge_search_run)
        params = list(sig.parameters.keys())
        assert params == ["run_key"], f"schedule_judge_search_run signature drifted: {params}"

    def test_schedule_returns_future(self) -> None:
        future = judges.schedule_judge_search_run("nonexistent-run-key")
        assert isinstance(future, Future)
        # Let it settle so the executor doesn't leak across tests.
        future.result(timeout=10)
