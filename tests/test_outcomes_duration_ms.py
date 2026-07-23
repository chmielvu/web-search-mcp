"""Unit tests for search outcome duration_ms truthiness (BUG5)."""

from __future__ import annotations

from types import SimpleNamespace

from kindly_web_search_mcp_server.search import outcomes as outcomes_module


def _build_duration(dc_total, timings: dict[str, float]) -> float:
    """Mirror persist_search_outcome duration selection without DuckDB I/O."""
    outcome = SimpleNamespace(timings=timings)
    dc = SimpleNamespace(total_latency_ms=dc_total)
    return dc.total_latency_ms if dc.total_latency_ms is not None else sum(outcome.timings.values())


def test_duration_ms_zero_latency_persists_zero() -> None:
    assert _build_duration(0.0, {"a": 10.0, "b": 20.0}) == 0.0


def test_duration_ms_none_falls_through_to_timings_sum() -> None:
    assert _build_duration(None, {"a": 10.0, "b": 20.0}) == 30.0


def test_duration_ms_positive_latency_used() -> None:
    assert _build_duration(1234.5, {"a": 1.0}) == 1234.5


def test_persist_search_outcome_duration_expression_in_source() -> None:
    """Guard the production expression against regressing to `or` truthiness."""
    import inspect

    src = inspect.getsource(outcomes_module.persist_search_outcome)
    assert "if dc.total_latency_ms is not None" in src
    assert "dc.total_latency_ms or sum" not in src
