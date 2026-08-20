"""Test that the dashboard JSONs are valid and use the current (v2, 2026-07-29) metric names and panel titles."""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dashboard(name: str) -> dict:
    path = _REPO_ROOT / "grafana" / "dashboards" / f"kindly-mcp-{name}-dashboard.json"
    assert path.exists(), f"dashboard json must exist: {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("title"), "title required"
    return data


def _titles(data: dict) -> list[str]:
    return [p.get("title", "") for p in data.get("panels", [])]


def _exprs(data: dict, title: str) -> list[str]:
    panel = next(p for p in data["panels"] if p.get("title") == title)
    out: list[str] = []
    for target in panel.get("targets", []):
        expr = target.get("expr", "")
        if isinstance(expr, list):
            out.extend(e for e in expr if isinstance(e, str))
        elif isinstance(expr, str):
            out.append(expr)
    return out


def test_quality_dashboard_json_parses_and_has_otel_quality_panels():
    data = _load_dashboard("quality")
    titles = _titles(data)

    # v2 quality dashboard: OTel-fed NDCG/judge/rerank panels (no semantic cache).
    for expected in (
        "Avg NDCG@10",
        "Avg Judge Score",
        "Domain Diversity",
        "Provider Overlap Rate",
        "Rerank Compression",
        "Judge Evaluations",
        "NDCG@10 Over Time",
        "Judge Score Distribution",
        "RRF Score Distribution",
        "Judge Score Histogram",
        "Quality Grade",
        "Quality Tier Distribution",
        "Judge Evaluation Details",
    ):
        assert expected in titles, f"missing panel {expected!r}; titles: {titles}"

    # Metric names must be the current OTel ones.
    all_exprs = " ".join(e for t in titles for e in _exprs(data, t))
    assert "search_ndcg_at_10" in all_exprs
    assert "judge_evaluation_overall_score" in all_exprs
    assert "search_rrf_score_bucket" in all_exprs

    semantic_titles = [t for t in titles if "semantic cache" in t.lower()]
    assert not semantic_titles, f"semantic cache panels must not be present: {semantic_titles}"


def test_overview_dashboard_json_parses_and_has_golden_signal_panels():
    data = _load_dashboard("overview")
    titles = _titles(data)

    for expected in (
        "Request Rate (5m)",
        "Error Rate (5m)",
        "p95 Latency",
        "LLM Cost Rate",
        "Active Providers",
        "Cache Hit Rate",
        "Request & Error Rate",
        "Latency Percentiles",
        "Token Usage by Purpose",
        "Cost by Provider",
        "Provider Health Status",
    ):
        assert expected in titles, f"missing panel {expected!r}; titles: {titles}"

    # Golden-signal panels must be service-scoped.
    all_exprs = " ".join(e for t in titles for e in _exprs(data, t))
    assert 'service="${service}"' in all_exprs


def test_pipeline_dashboard_json_uses_current_rerank_metric_names():
    data = _load_dashboard("pipeline")
    titles = _titles(data)

    for expected in (
        "Rewrite Rate (5m)",
        "Rerank Rate (5m)",
        "Avg Candidates In",
        "Avg Results Out",
        "Bi-Encoder p95",
        "Cross-Encoder p95",
        "Pipeline Stage Latency (p95)",
        "Candidates Through Pipeline",
        "Rewrite Latency",
        "Score Distribution by Stage",
        "Stage Compression Ratio",
        "Entity Overlap Score",
        "Diversity Filter Rate",
        "Avg Recency Boost",
        "Stage Performance Summary",
    ):
        assert expected in titles, f"missing panel {expected!r}; titles: {titles}"

    all_exprs = " ".join(e for t in titles for e in _exprs(data, t))
    assert "rerank_stage_duration_seconds_bucket" in all_exprs
    # The old RRF provider-contribution counter was removed in the v2 overhaul.
    assert "web_search_rrf_provider_contribution_total" not in all_exprs


def test_content_dashboard_json_uses_crawl4ai_stage_label():
    data = _load_dashboard("content")
    titles = _titles(data)

    for expected in (
        "Content Resolutions (5m)",
        "Crawl4AI Usage %",
        "Fallback Rate (5m)",
        "Avg Word Count",
        "Avg Size (bytes)",
        "Success Rate",
        "Resolutions by Stage",
        "Extraction Latency (p95)",
        "Fallbacks Over Time",
        "Word Count Distribution",
        "Stage Distribution",
        "Avg Fallback Count",
        "Stage Performance",
    ):
        assert expected in titles, f"missing panel {expected!r}; titles: {titles}"

    exprs = _exprs(data, "Crawl4AI Usage %")
    assert any('content_stage="crawl4ai"' in expr for expr in exprs), exprs
    assert all("browser_nodriver" not in expr for expr in exprs), exprs


def test_provider_dashboard_json_includes_circuit_state_panels():
    data = _load_dashboard("providers")
    titles = _titles(data)

    for expected in (
        "Provider Request Rate",
        "Provider p95 Latency",
        "Provider Success Rate",
        "Provider Error Rate",
        "Provider Freshness (seconds since last success)",
        "Circuit Breaker State Changes",
        "Results Returned per Provider",
        "Provider Health Summary",
    ):
        assert expected in titles, f"missing panel {expected!r}; titles: {titles}"

    exprs = _exprs(data, "Circuit Breaker State Changes")
    assert any("circuit_breaker_transitions_total" in expr for expr in exprs), exprs
