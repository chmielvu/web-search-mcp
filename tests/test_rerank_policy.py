"""Tests for rerank bypass policy (Task 3.2)."""

from __future__ import annotations

from kindly_web_search_mcp_server.rerank.policy import (
    decide_rerank,
    RerankDecision,
)


def test_policy_bypass_low_candidate_count():
    d = decide_rerank(query="foo bar", candidate_count=3, top_k=10)
    assert isinstance(d, RerankDecision)
    assert d.should_rerank is False
    assert d.reason in {"low_candidate_count", "below_top_k"}
    assert d.candidate_count == 3


def test_policy_rerank_sufficient_count():
    d = decide_rerank(query="foo bar baz", candidate_count=20, top_k=10)
    assert d.should_rerank is True
    assert d.reason == "eligible"
    assert d.candidate_count == 20


def test_policy_bypass_exact_literal():
    # quoted error or stack literal should bypass (no semantic gain)
    d = decide_rerank(query='FileNotFoundError: "config.json" at line 42', candidate_count=15)
    assert d.should_rerank is False
    assert d.reason == "exact_literal"


def test_policy_bypass_navigational_exact_domain():
    d = decide_rerank(query="site:github.com/owner/repo issue 1234", candidate_count=12)
    assert d.should_rerank is False
    assert d.reason == "navigational_exact_domain"


def test_policy_bypass_degraded_engine_health(monkeypatch):
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.rerank.policy.get_rerank_engine_health",
        lambda: {"status": "degraded", "cooldown_remaining": 30},
    )
    d = decide_rerank(query="normal research query here", candidate_count=25)
    assert d.should_rerank is False
    assert d.reason == "degraded_engine_health"
    assert d.engine_health["status"] == "degraded"


def test_policy_bypass_harmful_query_class(monkeypatch):
    # eval-proven harmful class from query policy / classifier
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.rerank.policy.classify_query_risk",
        lambda q: "harmful",
    )
    d = decide_rerank(query="how to exploit buffer overflow in prod", candidate_count=30)
    assert d.should_rerank is False
    assert d.reason == "harmful_query_class"


def test_policy_emits_eligibility_and_bypass_events(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.INFO)
    from kindly_web_search_mcp_server.rerank.policy import decide_rerank

    decide_rerank(query="short", candidate_count=2)
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "rerank.eligibility" in logged or "rerank.bypassed" in logged


def test_policy_returns_typed_fields():
    d = decide_rerank(query="q", candidate_count=10)
    assert hasattr(d, "should_rerank")
    assert hasattr(d, "reason")
    assert hasattr(d, "query_type")
    assert hasattr(d, "candidate_count")
    assert hasattr(d, "engine_health")
