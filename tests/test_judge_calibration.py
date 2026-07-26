"""Tests for the judge runner and calibration workflow.

All LLM calls are mocked — no real API calls in these unit tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest


# =========================================================================
# Helper: build a fake litellm acompletion response
# =========================================================================


def _make_fake_litellm_response(content: str) -> SimpleNamespace:
    """Build a minimal mock of a litellm acompletion return value."""
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


# =========================================================================
# Test 1: Full judge flow with mocked LLM
# =========================================================================


class TestJudgerunnerMockedLLM:
    """Tests for run_judge_evaluation with a mocked LLM response."""

    VALID_JUDGE_JSON = {
        "relevance_raw": 4,
        "relevance_score": 1.0,
        "reasoning": "Excellent results overall.",
        "judge_model": "openai/gpt-oss-120b",
        "duration_ms": 123.45,
    }

    @staticmethod
    def _make_result(title: str, link: str, snippet: str) -> SimpleNamespace:
        return SimpleNamespace(title=title, link=link, snippet=snippet)

    def test_happy_path(self, monkeypatch) -> None:
        """Full end-to-end: mocked adapter → parsed scores → stored in DuckDB."""
        from kindly_web_search_mcp_server.analytics.judge_runner import (
            run_judge_evaluation,
        )
        from kindly_web_search_mcp_server.analytics.search_relevance_judge import (
            SearchRelevanceResult,
        )

        fake_result = SearchRelevanceResult(
            relevance_grade="excellent",
            accuracy_grade="excellent",
            completeness_grade="excellent",
            source_quality_grade="excellent",
            relevance_score=1.0,
            accuracy_score=1.0,
            completeness_score=1.0,
            source_quality_score=1.0,
            overall_score=1.0,
            rationale="Excellent results overall.",
            judge_model="openai/gpt-oss-120b",
            duration_ms=123.45,
        )

        async def fake_evaluate(*args, **kwargs):
            return fake_result

        monkeypatch.setattr(
            "kindly_web_search_mcp_server.analytics.judge_runner._get_judge",
            lambda: SimpleNamespace(evaluate=fake_evaluate),
        )

        # Enable analytics and use a temp DuckDB path
        monkeypatch.setattr(
            "kindly_web_search_mcp_server.analytics.judge_runner.settings.analytics_enabled",
            True,
        )
        db_path = Path("test_judge_runner_mocked.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            monkeypatch.setattr(
                "kindly_web_search_mcp_server.analytics.judge_runner.settings.analytics_duckdb_path",
                str(db_path),
            )

            results = [
                self._make_result("Paper A", "https://a.example", "Great paper"),
                self._make_result("Paper B", "https://b.example", "Interesting work"),
            ]

            # This is async — we need to run it
            import asyncio

            asyncio.run(
                run_judge_evaluation(
                    run_key="test-run-001",
                    query="AI papers",
                    intent="general",
                    results=results,
                    tool_name="web_search",
                    research_goal="Find recent papers on AI safety",
                    rewrite_variants=None,
                )
            )

            # Verify the data was inserted into DuckDB
            import duckdb

            con = duckdb.connect(str(db_path))
            try:
                row = con.execute(
                    "SELECT run_key, relevance_grade, relevance_score, overall_score, "
                    "rationale, duration_ms, tool_name, judge_model "
                    "FROM judge_evaluations WHERE run_key = 'test-run-001'"
                ).fetchone()

                assert row is not None, "Expected a row in judge_evaluations"
                assert row[0] == "test-run-001"
                assert row[1] == "excellent"
                assert row[2] == 1.0
                assert row[3] == 1.0
                assert row[4] == "Excellent results overall."
                assert row[6] == "web_search"
                assert row[7] is not None
            finally:
                con.close()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_string_generation_is_parsed_into_four_dimensions(self, monkeypatch) -> None:
        import asyncio
        from kindly_web_search_mcp_server.analytics import search_relevance_judge as module

        content = json.dumps(
            {
                "relevance": {"grade": "good", "score": 0.8, "rationale": "on topic"},
                "accuracy": {"grade": "good", "score": 0.8, "rationale": "correct"},
                "completeness": {"grade": "fair", "score": 0.6, "rationale": "partial"},
                "source_quality": {"grade": "good", "score": 0.8, "rationale": "credible"},
                "overall_score": 0.75,
                "overall_rationale": "Useful result set.",
            }
        )

        class _Router:
            async def complete_json(self, **kwargs):
                return SimpleNamespace(
                    content=content,
                    model_used="model-4d",
                    input_tokens=12,
                    output_tokens=8,
                    endpoint=SimpleNamespace(name="groq"),
                )

        monkeypatch.setattr(module, "build_worker_router", lambda: _Router())
        judge = module.SearchRelevanceJudge(model="model-4d")
        result = asyncio.run(
            judge.evaluate(
                query="FastAPI docs",
                intent="general",
                results=[self._make_result("FastAPI", "https://fastapi.tiangolo.com", "docs")],
            )
        )

        assert result.status == "success"
        assert result.relevance_score == 0.8
        assert result.overall_score == 0.75
        assert result.provider_name == "groq"

    def test_malformed_string_is_auditable_error(self, monkeypatch) -> None:
        import asyncio
        from kindly_web_search_mcp_server.analytics import search_relevance_judge as module

        class _Router:
            async def complete_json(self, **kwargs):
                return SimpleNamespace(content="not json", model_used="model-4d")

        monkeypatch.setattr(module, "build_worker_router", lambda: _Router())
        judge = module.SearchRelevanceJudge(model="model-4d")
        result = asyncio.run(
            judge.evaluate(
                query="FastAPI docs",
                intent="general",
                results=[self._make_result("FastAPI", "https://fastapi.tiangolo.com", "docs")],
            )
        )

        assert result.status == "error"
        assert result.error_type == "parse_or_provider_error"
        assert result.relevance_score == 0.0

    def test_empty_results_skips_evaluation(self, monkeypatch) -> None:
        """No results should cause early return without LLM call."""
        from kindly_web_search_mcp_server.analytics.judge_runner import (
            run_judge_evaluation,
        )

        call_count = 0

        async def fake_evaluate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return SimpleNamespace(relevance_raw=1, relevance_score=0.0)

        monkeypatch.setattr(
            "kindly_web_search_mcp_server.analytics.judge_runner._get_judge",
            lambda: SimpleNamespace(evaluate=fake_evaluate),
        )

        import asyncio

        asyncio.run(
            run_judge_evaluation(
                run_key="test-empty",
                query="nothing",
                intent="general",
                results=[],
                tool_name="web_search",
            )
        )

        assert call_count == 0, "Judge should not be called with empty results"

    def test_langfuse_context_is_forwarded_to_judge(self, monkeypatch) -> None:
        from kindly_web_search_mcp_server.analytics.judge_runner import (
            run_judge_evaluation,
        )

        captured: dict[str, object] = {}

        async def fake_evaluate(*args, **kwargs):
            captured["langfuse"] = kwargs.get("langfuse")
            return SimpleNamespace(
                relevance_raw=4,
                relevance_score=1.0,
                reasoning="ok",
                judge_model="openai/gpt-oss-120b",
                duration_ms=1.0,
            )

        monkeypatch.setattr(
            "kindly_web_search_mcp_server.analytics.judge_runner._get_judge",
            lambda: SimpleNamespace(evaluate=fake_evaluate),
        )
        monkeypatch.setattr(
            "kindly_web_search_mcp_server.analytics.judge_runner.settings.analytics_enabled",
            True,
        )
        monkeypatch.setattr(
            "kindly_web_search_mcp_server.analytics.judge_runner.insert_judge_evaluation",
            lambda *args, **kwargs: None,
        )

        import asyncio

        asyncio.run(
            run_judge_evaluation(
                run_key="test-run-002",
                query="AI papers",
                intent="general",
                results=[self._make_result("Paper A", "https://a.example", "Great paper")],
                tool_name="web_search",
                session_id="session-999",
            )
        )

        langfuse = captured["langfuse"]
        assert langfuse is not None
        assert langfuse.trace_name == "judge:web_search"
        assert langfuse.session_id == "session-999"
        assert langfuse.metadata["task"] == "judge"
        assert langfuse.metadata["run_key"] == "test-run-002"

    def test_llm_failure_inserts_fallback(self, monkeypatch) -> None:
        """When the judge raises, a fallback row should still be inserted."""
        from kindly_web_search_mcp_server.analytics.judge_runner import (
            run_judge_evaluation,
        )
        from kindly_web_search_mcp_server.analytics.search_relevance_judge import (
            SearchRelevanceResult,
        )

        async def failing_evaluate(*args, **kwargs):
            return SearchRelevanceResult(
                relevance_grade="poor",
                accuracy_grade="poor",
                completeness_grade="poor",
                source_quality_grade="poor",
                relevance_score=0.0,
                accuracy_score=0.0,
                completeness_score=0.0,
                source_quality_score=0.0,
                overall_score=0.0,
                rationale="",
                judge_model="openai/gpt-oss-120b",
                duration_ms=0.0,
                error="RuntimeError: LLM is down",
            )

        monkeypatch.setattr(
            "kindly_web_search_mcp_server.analytics.judge_runner._get_judge",
            lambda: SimpleNamespace(evaluate=failing_evaluate),
        )
        monkeypatch.setattr(
            "kindly_web_search_mcp_server.analytics.judge_runner.settings.analytics_enabled",
            True,
        )

        db_path = Path("test_judge_runner_fallback.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            monkeypatch.setattr(
                "kindly_web_search_mcp_server.analytics.judge_runner.settings.analytics_duckdb_path",
                str(db_path),
            )

            results = [SimpleNamespace(title="Test", link="https://t.example", snippet="test")]

            import asyncio

            asyncio.run(
                run_judge_evaluation(
                    run_key="test-fallback",
                    query="test",
                    intent="general",
                    results=results,
                )
            )

            import duckdb

            con = duckdb.connect(str(db_path))
            try:
                row = con.execute(
                    "SELECT run_key, overall_score, payload_json "
                    "FROM judge_evaluations WHERE run_key = 'test-fallback'"
                ).fetchone()

                assert row is not None, "Expected a fallback row"
                assert row[0] == "test-fallback"
                import json as _json

                payload = _json.loads(row[2])
                assert "error" in payload
            finally:
                con.close()
        finally:
            if db_path.exists():
                db_path.unlink()


# =========================================================================
# Test 2: Calibration workflow
# =========================================================================


class TestJudgeCalibration:
    """Tests for calibrate_judge."""

    def _seed_judge_scores(
        self,
        db_path: Path,
        data: list[dict],
    ) -> None:
        """Insert judge_evaluation rows for calibration testing."""
        import duckdb

        con = duckdb.connect(str(db_path))
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS judge_evaluations (
                    run_key VARCHAR NOT NULL,
                    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    tool_name VARCHAR,
                    judge_model VARCHAR,
                    relevance_score DOUBLE,
                    relevance_raw INTEGER,
                    relevance_scale VARCHAR,
                    accuracy_score DOUBLE,
                    completeness_score DOUBLE,
                    source_quality_score DOUBLE,
                    overall_score DOUBLE,
                    rationale VARCHAR,
                    duration_ms DOUBLE,
                    tokens_used INTEGER,
                    cost_usd DOUBLE,
                    payload_json JSON
                )
                """
            )
            for row in data:
                con.execute(
                    """
                    INSERT INTO judge_evaluations
                        (run_key, tool_name, judge_model,
                         relevance_score, relevance_raw, relevance_scale,
                         accuracy_score, completeness_score,
                         source_quality_score, overall_score,
                         rationale, duration_ms,
                         tokens_used, cost_usd, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("run_key"),
                        row.get("tool_name", "web_search"),
                        row.get("judge_model", "test-model"),
                        row.get("relevance_score"),
                        row.get("relevance_raw"),
                        row.get("relevance_scale"),
                        row.get("accuracy_score"),
                        row.get("completeness_score"),
                        row.get("source_quality_score"),
                        row.get("overall_score"),
                        row.get("rationale"),
                        row.get("duration_ms"),
                        row.get("tokens_used"),
                        row.get("cost_usd"),
                        json.dumps(row.get("payload_json", {})),
                    ),
                )
        finally:
            con.close()

    def test_calibrate_perfect_correlation(self) -> None:
        """When actual == expected, correlation should be 1.0 and bias 0."""
        db_path = Path("test_calibrate_perfect.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            from kindly_web_search_mcp_server.analytics.judge_calibration import (
                calibrate_judge,
            )

            # Seed data where actual matches expected perfectly
            self._seed_judge_scores(
                db_path,
                [
                    {
                        "run_key": "rk-c-perfect-1",
                        "overall_score": 0.9,
                        "relevance_score": 0.9,
                        "accuracy_score": 0.85,
                        "completeness_score": 0.8,
                        "source_quality_score": 0.85,
                    },
                    {
                        "run_key": "rk-c-perfect-2",
                        "overall_score": 0.7,
                        "relevance_score": 0.7,
                        "accuracy_score": 0.65,
                        "completeness_score": 0.6,
                        "source_quality_score": 0.75,
                    },
                    {
                        "run_key": "rk-c-perfect-3",
                        "overall_score": 0.5,
                        "relevance_score": 0.5,
                        "accuracy_score": 0.55,
                        "completeness_score": 0.5,
                        "source_quality_score": 0.5,
                    },
                ],
            )

            known_queries = [
                {
                    "run_key": "rk-c-perfect-1",
                    "expected_scores": {
                        "overall_score": 0.9,
                        "relevance_score": 0.9,
                        "accuracy_score": 0.85,
                        "completeness_score": 0.8,
                        "source_quality_score": 0.85,
                    },
                },
                {
                    "run_key": "rk-c-perfect-2",
                    "expected_scores": {
                        "overall_score": 0.7,
                        "relevance_score": 0.7,
                        "accuracy_score": 0.65,
                        "completeness_score": 0.6,
                        "source_quality_score": 0.75,
                    },
                },
                {
                    "run_key": "rk-c-perfect-3",
                    "expected_scores": {
                        "overall_score": 0.5,
                        "relevance_score": 0.5,
                        "accuracy_score": 0.55,
                        "completeness_score": 0.5,
                        "source_quality_score": 0.5,
                    },
                },
            ]

            result = calibrate_judge(known_queries, db_path=str(db_path))

            assert result["n"] == 3
            assert result["correlation"] == pytest.approx(1.0, abs=1e-6)
            assert result["mean_absolute_error"] == pytest.approx(0.0, abs=1e-6)
            assert result["bias"] == pytest.approx(0.0, abs=1e-6)

            # Per-dimension checks
            assert result["per_dimension"]["overall_score"]["mae"] == 0.0
            assert result["per_dimension"]["overall_score"]["bias"] == 0.0
            assert result["per_dimension"]["relevance_score"]["mae"] == 0.0
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_calibrate_with_bias(self) -> None:
        """When actual consistently overshoots, bias should be positive."""
        db_path = Path("test_calibrate_bias.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            from kindly_web_search_mcp_server.analytics.judge_calibration import (
                calibrate_judge,
            )

            # Actual scores are 0.1 higher than expected (positive bias)
            self._seed_judge_scores(
                db_path,
                [
                    {
                        "run_key": "rk-bias-1",
                        "overall_score": 0.8,
                        "relevance_score": 0.8,
                        "accuracy_score": 0.8,
                        "completeness_score": 0.8,
                        "source_quality_score": 0.8,
                    },
                    {
                        "run_key": "rk-bias-2",
                        "overall_score": 0.6,
                        "relevance_score": 0.6,
                        "accuracy_score": 0.6,
                        "completeness_score": 0.6,
                        "source_quality_score": 0.6,
                    },
                    {
                        "run_key": "rk-bias-3",
                        "overall_score": 0.4,
                        "relevance_score": 0.4,
                        "accuracy_score": 0.4,
                        "completeness_score": 0.4,
                        "source_quality_score": 0.4,
                    },
                ],
            )

            known_queries = [
                {
                    "run_key": "rk-bias-1",
                    "expected_scores": {
                        "overall_score": 0.7,
                        "relevance_score": 0.7,
                        "accuracy_score": 0.7,
                        "completeness_score": 0.7,
                        "source_quality_score": 0.7,
                    },
                },
                {
                    "run_key": "rk-bias-2",
                    "expected_scores": {
                        "overall_score": 0.5,
                        "relevance_score": 0.5,
                        "accuracy_score": 0.5,
                        "completeness_score": 0.5,
                        "source_quality_score": 0.5,
                    },
                },
                {
                    "run_key": "rk-bias-3",
                    "expected_scores": {
                        "overall_score": 0.3,
                        "relevance_score": 0.3,
                        "accuracy_score": 0.3,
                        "completeness_score": 0.3,
                        "source_quality_score": 0.3,
                    },
                },
            ]

            result = calibrate_judge(known_queries, db_path=str(db_path))

            assert result["n"] == 3
            # Perfect correlation (all shifted by same amount)
            assert result["correlation"] == pytest.approx(1.0, abs=1e-6)
            # MAE = 0.1
            assert result["mean_absolute_error"] == pytest.approx(0.1, abs=1e-6)
            # Bias = +0.1
            assert result["bias"] == pytest.approx(0.1, abs=1e-6)
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_calibrate_negative_bias(self) -> None:
        """When actual undershoots, bias should be negative."""
        db_path = Path("test_calibrate_neg_bias.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            from kindly_web_search_mcp_server.analytics.judge_calibration import (
                calibrate_judge,
            )

            # Actual scores are 0.2 lower than expected (negative bias)
            self._seed_judge_scores(
                db_path,
                [
                    {
                        "run_key": "rk-neg-1",
                        "overall_score": 0.5,
                    },
                    {
                        "run_key": "rk-neg-2",
                        "overall_score": 0.7,
                    },
                ],
            )

            known_queries = [
                {
                    "run_key": "rk-neg-1",
                    "expected_scores": {"overall_score": 0.7},
                },
                {
                    "run_key": "rk-neg-2",
                    "expected_scores": {"overall_score": 0.9},
                },
            ]

            result = calibrate_judge(known_queries, db_path=str(db_path))

            assert result["n"] == 2
            assert result["bias"] == pytest.approx(-0.2, abs=1e-6)
            assert result["mean_absolute_error"] == pytest.approx(0.2, abs=1e-6)
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_calibrate_imperfect_correlation(self) -> None:
        """Verify correlation is computed correctly with non-perfect data."""
        db_path = Path("test_calibrate_corr.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            from kindly_web_search_mcp_server.analytics.judge_calibration import (
                calibrate_judge,
            )

            # Deliberately messy: expected [0.9, 0.7, 0.5], actual [0.85, 0.6, 0.55]
            # Pearson correlation between those should be > 0.9
            self._seed_judge_scores(
                db_path,
                [
                    {"run_key": "rk-c-1", "overall_score": 0.85},
                    {"run_key": "rk-c-2", "overall_score": 0.6},
                    {"run_key": "rk-c-3", "overall_score": 0.55},
                ],
            )

            known_queries = [
                {"run_key": "rk-c-1", "expected_scores": {"overall_score": 0.9}},
                {"run_key": "rk-c-2", "expected_scores": {"overall_score": 0.7}},
                {"run_key": "rk-c-3", "expected_scores": {"overall_score": 0.5}},
            ]

            result = calibrate_judge(known_queries, db_path=str(db_path))

            assert result["n"] == 3
            # Manually computed: expected [0.9, 0.7, 0.5], actual [0.85, 0.6, 0.55]
            # Pearson ≈ 0.933
            assert result["correlation"] == pytest.approx(0.933, abs=0.01)
            assert result["bias"] == pytest.approx(-0.03333, abs=0.01)
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_calibrate_no_matching_rows(self) -> None:
        """When no rows match, metrics should be 0 with n=0."""
        db_path = Path("test_calibrate_empty.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            from kindly_web_search_mcp_server.analytics.judge_calibration import (
                calibrate_judge,
            )

            known_queries = [
                {
                    "run_key": "rk-nonexistent-1",
                    "expected_scores": {"overall_score": 0.9},
                },
            ]

            result = calibrate_judge(known_queries, db_path=str(db_path))

            assert result["n"] == 0
            assert result["correlation"] == 0.0
            assert result["mean_absolute_error"] == 0.0
            assert result["bias"] == 0.0
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_calibrate_partial_missing_rows(self) -> None:
        """Only matching rows should be included in the computation."""
        db_path = Path("test_calibrate_partial.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            from kindly_web_search_mcp_server.analytics.judge_calibration import (
                calibrate_judge,
            )

            self._seed_judge_scores(
                db_path,
                [
                    {
                        "run_key": "rk-partial-1",
                        "overall_score": 0.8,
                        "relevance_score": 0.8,
                    },
                    {
                        "run_key": "rk-partial-2",
                        "overall_score": 0.6,
                        "relevance_score": 0.6,
                    },
                ],
            )

            known_queries = [
                {
                    "run_key": "rk-partial-1",
                    "expected_scores": {
                        "overall_score": 0.7,
                        "relevance_score": 0.7,
                    },
                },
                {
                    "run_key": "rk-partial-2",
                    "expected_scores": {
                        "overall_score": 0.5,
                        "relevance_score": 0.5,
                    },
                },
                {
                    "run_key": "rk-partial-nonexistent",
                    "expected_scores": {"overall_score": 0.9},
                },
            ]

            result = calibrate_judge(known_queries, db_path=str(db_path))

            # Only 2 of 3 queries have matching data
            assert result["n"] == 2
            assert result["bias"] == pytest.approx(0.1, abs=1e-6)
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_calibrate_single_query(self) -> None:
        """With a single data point, correlation defaults to 0.0."""
        db_path = Path("test_calibrate_single.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            from kindly_web_search_mcp_server.analytics.judge_calibration import (
                calibrate_judge,
            )

            self._seed_judge_scores(
                db_path,
                [
                    {
                        "run_key": "rk-single",
                        "overall_score": 0.75,
                    },
                ],
            )

            known_queries = [
                {
                    "run_key": "rk-single",
                    "expected_scores": {"overall_score": 0.7},
                },
            ]

            result = calibrate_judge(known_queries, db_path=str(db_path))

            assert result["n"] == 1
            assert result["correlation"] == 0.0  # can't compute with n < 2
            assert result["mean_absolute_error"] == pytest.approx(0.05, abs=1e-6)
            assert result["bias"] == pytest.approx(0.05, abs=1e-6)
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_calibrate_per_dimension_metrics(self) -> None:
        """Verify per-dimension MAE and bias are reported correctly."""
        db_path = Path("test_calibrate_perdim.duckdb")
        if db_path.exists():
            db_path.unlink()
        try:
            from kindly_web_search_mcp_server.analytics.judge_calibration import (
                calibrate_judge,
            )

            self._seed_judge_scores(
                db_path,
                [
                    {
                        "run_key": "rk-pd-1",
                        "relevance_score": 0.95,
                        "accuracy_score": 0.90,
                        "completeness_score": 0.85,
                        "source_quality_score": 0.80,
                        "overall_score": 0.88,
                    },
                ],
            )

            known_queries = [
                {
                    "run_key": "rk-pd-1",
                    "expected_scores": {
                        "relevance_score": 0.90,
                        "accuracy_score": 0.85,
                        "completeness_score": 0.80,
                        "source_quality_score": 0.75,
                        "overall_score": 0.83,
                    },
                },
            ]

            result = calibrate_judge(known_queries, db_path=str(db_path))

            pd = result["per_dimension"]
            # All should have mae=0.05, bias=0.05
            for dim in [
                "relevance_score",
                "accuracy_score",
                "completeness_score",
                "source_quality_score",
                "overall_score",
            ]:
                assert pd[dim]["mae"] == pytest.approx(0.05, abs=1e-6)
                assert pd[dim]["bias"] == pytest.approx(0.05, abs=1e-6)
        finally:
            if db_path.exists():
                db_path.unlink()
