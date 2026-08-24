"""Tests for the deterministic query-understanding fallback extractor."""

from __future__ import annotations

import time

import httpx
import pytest

from kindly_web_search_mcp_server.settings import settings

from kindly_web_search_mcp_server.entity.gliner_client import GLiNER2Client
from kindly_web_search_mcp_server.heuristics.understanding_fallback import (
    resolve_fallback_understanding,
)
from kindly_web_search_mcp_server.search.understanding.resolver import (
    _deterministic_fallback,
)

CASES = [
    ("FastAPI docs vs Starlette docs", "comparison", ("FastAPI docs", "Starlette docs"), True),
    ("fastapi and starlette comparison", "comparison", ("fastapi", "starlette comparison"), True),
    ("compare fastapi and starlette", "comparison", ("fastapi", "starlette"), True),
    (
        "compare fastapi vs starlette vs flask",
        "comparison",
        ("fastapi", "starlette vs flask"),
        True,
    ),
    ("latest python 3.13 release notes", "news", (), False),
    ("vs code extensions for python", "ai_coding_and_infrastructure", (), False),
    ("recent historical data", "general", (), False),
    ("x vs", "general", (), False),
    ("vs", "general", (), False),
    ("", "general", (), False),
    ("  FastAPI docs vs Starlette docs  ", "comparison", ("FastAPI docs", "Starlette docs"), True),
]


@pytest.mark.parametrize("query,intent,compared,decompose", CASES)
def test_functional_cases(query: str, intent: str, compared: tuple[str, ...], decompose: bool):
    result = resolve_fallback_understanding(query)
    assert result.intent == intent
    assert result.compared_entities == compared
    assert result.should_decompose == decompose


@pytest.mark.parametrize("query", [case[0] for case in CASES])
def test_offset_fidelity(query: str):
    result = resolve_fallback_understanding(query)
    for (start, end), surface in zip(result.compared_spans, result.compared_entities):
        assert start >= 0 and end <= len(query)
        assert query[start:end] == surface


def test_product_vs_code_is_not_comparison():
    result = resolve_fallback_understanding("vs code extensions for python")
    assert result.intent != "comparison"
    assert result.compared_entities == ()


def test_compare_vs_code_extensions_is_not_comparison():
    # Product exclusion must precede comparison-word checks: the word "compare"
    # alone does not make "VS Code" (the product) a comparison marker.
    result = resolve_fallback_understanding("compare VS Code extensions")
    assert result.intent == "general"
    assert result.compared_entities == ()


def test_vs_code_vs_x_abstains_precision_first():
    # Known precision-first abstention: "VS Code vs PyCharm" has a genuine
    # marker but the product-name exclusion fires first -> general (FN accepted,
    # never an FP). Documented in the design doc's limitations.
    result = resolve_fallback_understanding("VS Code vs PyCharm")
    assert result.intent == "general"
    assert result.compared_entities == ()


def test_single_letter_keyword_tokens_are_ignored():
    assert resolve_fallback_understanding("x vs").intent == "general"
    assert resolve_fallback_understanding("x").intent == "general"


def test_time_precedence_current_over_recent_over_historical():
    assert resolve_fallback_understanding("recent historical data").time_sensitivity == "recent"
    assert resolve_fallback_understanding("current recent data").time_sensitivity == "current"


def test_time_gate_and_year_fallback_path():
    hot = resolve_fallback_understanding("latest python release notes")
    assert hot.time_sensitivity == "current"
    assert hot.intent == "news"


def test_determinism():
    first = resolve_fallback_understanding("FastAPI docs vs Starlette docs")
    second = resolve_fallback_understanding("FastAPI docs vs Starlette docs")
    assert first == second


def test_resolver_fallback_maps_onto_query_understanding_result():
    analysis = _deterministic_fallback("gliner2-test", query="FastAPI docs vs Starlette docs")
    assert analysis.fallback is True
    assert analysis.error_reason == "gliner2-test"
    understanding = analysis.understanding
    assert understanding.intent == "comparison"
    assert understanding.compared_entities == ["FastAPI docs", "Starlette docs"]
    assert understanding.should_decompose is True
    assert understanding.time_sensitivity == "none"
    assert understanding.confidence == 0.0
    assert understanding.entities == []
    assert understanding.relations == []
    assert understanding.rationale.startswith("gliner2-test")


def test_gateway_fallback_result_preserves_reason_only_contract():
    client = GLiNER2Client(base_url="http://127.0.0.1:8000", timeout=1.0)
    analysis = client._fallback_result("gliner2-timeout", model="test", query="a query")
    assert analysis.fallback is True
    assert analysis.error_reason == "gliner2-timeout"
    assert analysis.understanding.intent == "general"
    assert analysis.understanding.rationale == "gliner2-timeout"
    assert analysis.understanding.compared_entities == []


def test_gateway_fallback_result_derives_comparison():
    client = GLiNER2Client(base_url="http://127.0.0.1:8000", timeout=1.0)
    analysis = client._fallback_result(
        "gliner2-timeout", model="test", query="FastAPI vs Starlette"
    )
    assert analysis.understanding.intent == "comparison"
    assert analysis.understanding.compared_entities == ["FastAPI", "Starlette"]
    assert analysis.understanding.rationale.startswith("gliner2-timeout")


@pytest.mark.asyncio
async def test_analyze_query_gateway_error_derives_comparison(monkeypatch):
    """Prove the derived comparison path through the async gateway outage path."""
    monkeypatch.setattr(settings, "intent_classifier_enabled", True)
    client = GLiNER2Client(base_url="http://127.0.0.1:8000", timeout=1.0)

    async def boom(self, path: str, payload: dict, *, operation: str):
        del self, path, payload, operation
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(GLiNER2Client, "_post", boom)
    analysis = await client.analyze_query("FastAPI vs Starlette")
    assert analysis.fallback is True
    assert analysis.error_reason == "gliner2-timeout"
    assert analysis.understanding.intent == "comparison"
    assert analysis.understanding.compared_entities == ["FastAPI", "Starlette"]
    assert analysis.understanding.should_decompose is True


def test_empty_query_stays_general():
    result = resolve_fallback_understanding("")
    assert result.intent == "general"
    assert result.compared_entities == ()
    assert result.time_sensitivity == "none"


def test_pathological_input_stays_bounded():
    big = ("vs vs vs " * 1111) + "v"
    started = time.perf_counter()
    result = resolve_fallback_understanding(big)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result.intent == "general"
    assert elapsed_ms < 100  # generous CI-safe bound; measured ~6ms locally
