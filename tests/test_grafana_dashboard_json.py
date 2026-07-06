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
    assert any(
        "tool profile" in t or "profile usage" in t or "tool_surface" in t for t in panel_lower
    ), f"missing tool profile usage panel; titles: {panels}"
    assert any(
        "result memory" in t or "result-memory" in t or "result_memory" in t for t in panel_lower
    ), f"missing result-memory panel; titles: {panels}"
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


def test_overview_dashboard_json_parses_and_has_loki_panels():
    path = Path("grafana/dashboards/kindly-mcp-overview-dashboard.json")
    assert path.exists(), "dashboard json must exist"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("title"), "title required"

    panels = data.get("panels", [])
    titles = [p.get("title", "") for p in panels]
    assert "Loki Log Lines (15m)" in titles, f"missing Loki log volume panel: {titles}"
    assert "OTLP Export 404s (1h)" in titles, f"missing OTLP panel: {titles}"
    assert "Loki ERROR Lines (1h)" in titles, f"missing Loki error panel: {titles}"

    loki_panels = [p for p in panels if p.get("title") in titles[-3:]]
    expressions = [
        target.get("expr", "") for panel in loki_panels for target in panel.get("targets", [])
    ]
    assert all('service_name="$service"' in expr for expr in expressions), expressions


def test_pipeline_dashboard_json_uses_current_rrf_metric_name():
    path = Path("grafana/dashboards/kindly-mcp-pipeline-dashboard.json")
    assert path.exists(), "dashboard json must exist"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)

    titles = [p.get("title", "") for p in data.get("panels", [])]
    assert "Avg Providers per Search" in titles, titles

    panel = next(p for p in data["panels"] if p.get("title") == "Avg Providers per Search")
    exprs = [target.get("expr", "") for target in panel.get("targets", [])]
    assert any("web_search_rrf_provider_contribution{" in expr for expr in exprs), exprs
    assert all("web_search_rrf_provider_contribution_total" not in expr for expr in exprs), exprs


def test_content_dashboard_json_uses_crawl4ai_remote_stage():
    path = Path("grafana/dashboards/kindly-mcp-content-dashboard.json")
    assert path.exists(), "dashboard json must exist"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)

    titles = [p.get("title", "") for p in data.get("panels", [])]
    assert "crawl4ai_remote Usage %" in titles, titles

    panel = next(p for p in data["panels"] if p.get("title") == "crawl4ai_remote Usage %")
    exprs = [target.get("expr", "") for target in panel.get("targets", [])]
    assert any('content_final_stage="crawl4ai_remote"' in expr for expr in exprs), exprs
    assert all("browser_nodriver" not in expr for expr in exprs), exprs


def test_provider_dashboard_json_includes_circuit_state_panels():
    path = Path("grafana/dashboards/kindly-mcp-providers-dashboard.json")
    assert path.exists(), "dashboard json must exist"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)

    titles = [p.get("title", "") for p in data.get("panels", [])]
    assert "Providers in Open/Half-Open State" in titles, titles
    assert "Current Circuit States" in titles, titles

    panel = next(p for p in data["panels"] if p.get("title") == "Providers in Open/Half-Open State")
    exprs = [target.get("expr", "") for target in panel.get("targets", [])]
    assert any("web_search_provider_circuit_state" in expr for expr in exprs), exprs
