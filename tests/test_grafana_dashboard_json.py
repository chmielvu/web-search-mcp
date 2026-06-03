"""Test that the quality dashboard JSON is valid and contains the Phase 9 joint panels using real event names (no semantic cache panels)."""

from __future__ import annotations

import json
from pathlib import Path


def test_quality_dashboard_json_parses_and_has_joint_panels():
    path = Path("grafana/dashboards/kindly-mcp-quality-dashboard.json")
    assert path.exists(), "dashboard json must exist"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("title"), "title required"

    panels = [p.get("title", "") for p in data.get("panels", [])]
    panel_lower = [t.lower() for t in panels]

    # Must have panels for the new areas (real events from phases 1-8 +9)
    assert any("tool profile" in t or "profile usage" in t or "tool_surface" in t for t in panel_lower), (
        f"missing tool profile usage panel; titles: {panels}"
    )
    assert any("result memory" in t or "result-memory" in t or "result_memory" in t for t in panel_lower), (
        f"missing result-memory panel; titles: {panels}"
    )
    assert any(
        ("rerank" in t and ("latency" in t or "quality" in t or "duration" in t))
        for t in panel_lower
    ), f"missing rerank latency/quality panel; titles: {panels}"
    assert any("eval" in t and ("pass" in t or "rate" in t) for t in panel_lower), (
        f"missing eval pass rate panel; titles: {panels}"
    )
    assert any("entity" in t and "latency" in t for t in panel_lower), (
        f"missing entity extraction latency panel; titles: {panels}"
    )

    # Explicitly no semantic cache panels per joint plan resolution
    semantic_titles = [t for t in panels if "semantic cache" in t.lower()]
    assert not semantic_titles, f"semantic cache panels must not be present: {semantic_titles}"
