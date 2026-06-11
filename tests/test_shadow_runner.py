from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# ShadowRunner tests
# ---------------------------------------------------------------------------


class TestShadowRunner:
    """Tests for run_shadow fire-and-forget A/B shadow execution."""

    def test_shadow_fn_called_with_correct_kwargs(self):
        """Shadow function must be invoked with the provided kwargs."""
        shadow_fn = AsyncMock(return_value="result")
        shadow_kwargs = {"query": "hello", "top_k": 5}

        with patch(
            "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run"
        ):
            asyncio.run(
                run_shadow(
                    run_key="rk-001",
                    experiment_id="exp-001",
                    variant="treatment",
                    layer="query_understanding",
                    shadow_fn=shadow_fn,
                    shadow_kwargs=shadow_kwargs,
                    control_duration_ms=100.0,
                )
            )

        shadow_fn.assert_awaited_once_with(query="hello", top_k=5)

    def test_shadow_result_recorded_in_duckdb(self):
        """insert_ab_shadow_run must be called with correct parameters."""
        shadow_fn = AsyncMock(return_value=["result1", "result2"])

        with patch(
            "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run"
        ) as mock_insert:
            asyncio.run(
                run_shadow(
                    run_key="rk-002",
                    experiment_id="exp-002",
                    variant="treatment_v2",
                    layer="reranking",
                    shadow_fn=shadow_fn,
                    shadow_kwargs={"query": "test"},
                    control_duration_ms=200.0,
                    control_result_summary={"num_results": 10},
                )
            )

        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args[1]

        assert call_kwargs["run_key"] == "rk-002"
        assert call_kwargs["experiment_id"] == "exp-002"
        assert call_kwargs["variant"] == "treatment_v2"
        assert call_kwargs["layer"] == "reranking"
        assert isinstance(call_kwargs["duration_ms"], float)
        assert call_kwargs["error_type"] is None

        payload = call_kwargs["payload_json"]
        assert isinstance(payload, dict)
        assert "control_duration_ms" in payload
        assert "latency_delta_ms" in payload
        assert payload["control_summary"] == {"num_results": 10}
        assert payload["shadow_summary"] == {"count": 2, "first_3": ["result1", "result2"]}

    def test_shadow_failure_records_with_error_type(self):
        """When shadow_fn raises, error_type must be set and no exception propagates."""
        shadow_fn = AsyncMock(side_effect=ValueError("shadow exploded"))

        with patch(
            "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run"
        ) as mock_insert:
            # Should not raise despite shadow_fn raising
            asyncio.run(
                run_shadow(
                    run_key="rk-003",
                    experiment_id="exp-003",
                    variant="treatment",
                    layer="search",
                    shadow_fn=shadow_fn,
                    shadow_kwargs={},
                    control_duration_ms=50.0,
                )
            )

        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args[1]
        assert call_kwargs["error_type"] == "shadow_failed"
        assert isinstance(call_kwargs["duration_ms"], float)

    def test_latency_delta_ms_computed_correctly(self):
        """latency_delta_ms must be shadow_duration - control_duration."""
        shadow_fn = AsyncMock(return_value="ok")

        with patch(
            "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run"
        ) as mock_insert:
            asyncio.run(
                run_shadow(
                    run_key="rk-004",
                    experiment_id="exp-004",
                    variant="treatment",
                    layer="query_understanding",
                    shadow_fn=shadow_fn,
                    shadow_kwargs={},
                    control_duration_ms=150.0,
                )
            )

        call_kwargs = mock_insert.call_args[1]
        payload = call_kwargs["payload_json"]
        # shadow took some real time (> 0), so latency_delta > -150
        assert payload["latency_delta_ms"] > -150
        assert payload["control_duration_ms"] == 150.0

    def test_never_propagates_exception_when_duckdb_fails(self):
        """Even if insert_ab_shadow_run raises, run_shadow must not propagate."""
        shadow_fn = AsyncMock(return_value="ok")

        with patch(
            "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run",
            side_effect=RuntimeError("db failure"),
        ):
            # Should not raise
            asyncio.run(
                run_shadow(
                    run_key="rk-005",
                    experiment_id="exp-005",
                    variant="treatment",
                    layer="reranking",
                    shadow_fn=shadow_fn,
                    shadow_kwargs={},
                    control_duration_ms=100.0,
                )
            )

    def test_never_propagates_exception_when_both_fail(self):
        """Even if shadow_fn AND insert_ab_shadow_run both fail, no exception propagates."""
        shadow_fn = AsyncMock(side_effect=TimeoutError("timeout"))

        with patch(
            "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run",
            side_effect=RuntimeError("db failure"),
        ):
            asyncio.run(
                run_shadow(
                    run_key="rk-006",
                    experiment_id="exp-006",
                    variant="treatment",
                    layer="query_understanding",
                    shadow_fn=shadow_fn,
                    shadow_kwargs={},
                    control_duration_ms=100.0,
                )
            )

    def test_asyncio_create_task_fire_and_forget(self):
        """The fire-and-forget pattern via asyncio.create_task must work."""
        async def _fire_and_forget():
            shadow_fn = AsyncMock(return_value="ok")
            mock_insert = MagicMock()

            with patch(
                "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run",
                mock_insert,
            ):
                task = asyncio.create_task(
                    run_shadow(
                        run_key="rk-007",
                        experiment_id="exp-007",
                        variant="treatment",
                        layer="search",
                        shadow_fn=shadow_fn,
                        shadow_kwargs={},
                        control_duration_ms=100.0,
                    )
                )
                # In the fire-and-forget pattern, control returns immediately
                # while the shadow runs in the background.
                # We simulate this by yielding control and then awaiting the task.
                await asyncio.sleep(0)
                await task

            shadow_fn.assert_awaited_once()
            assert mock_insert.called

        asyncio.run(_fire_and_forget())

    def test_safe_summary_with_dict(self):
        """_safe_summary handles dicts correctly."""
        from kindly_web_search_mcp_server.ab_testing.shadow_runner import _safe_summary

        obj = {"a": 1, "b": "hello", "c": [1, 2, 3]}
        result = _safe_summary(obj)
        assert result["a"] == 1
        assert result["b"] == "hello"
        assert result["c"] == "[1, 2, 3]"

    def test_safe_summary_with_list(self):
        """_safe_summary handles lists correctly."""
        from kindly_web_search_mcp_server.ab_testing.shadow_runner import _safe_summary

        obj = ["first", "second", "third", "fourth"]
        result = _safe_summary(obj)
        assert result["count"] == 4
        assert len(result["first_3"]) == 3

    def test_safe_summary_with_none(self):
        """_safe_summary handles None."""
        from kindly_web_search_mcp_server.ab_testing.shadow_runner import _safe_summary

        assert _safe_summary(None) is None

    def test_safe_summary_with_custom_object(self):
        """_safe_summary handles objects with __dict__."""
        from kindly_web_search_mcp_server.ab_testing.shadow_runner import _safe_summary

        class CustomObj:
            def __init__(self):
                self.name = "test"
                self.value = 42

        result = _safe_summary(CustomObj())
        assert result["name"] == "test"
        assert result["value"] == "42"

    def test_safe_summary_with_scalar(self):
        """_safe_summary falls back to str() for other types."""
        from kindly_web_search_mcp_server.ab_testing.shadow_runner import _safe_summary

        assert _safe_summary(123) == "123"
        assert _safe_summary(True) == "True"


# Import the function under test after sys.path is set
from kindly_web_search_mcp_server.ab_testing.shadow_runner import run_shadow