"""Rerank eval metrics tests (Phase 4.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kindly_web_search_mcp_server.evals.metrics import (
    mrr_at_k,
    ndcg_at_k,
    top_k_domain_hit,
    provider_survival_rate,
)


def _load_cases():
    p = Path("evals/rerank_cases.jsonl")
    cases = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def test_loads_at_least_10_seed_cases():
    cases = _load_cases()
    assert len(cases) >= 10


def test_mrr_at_k_basic():
    # gold at position 1 (0-index -> rank 2? mrr uses 1-based)
    ranked_links = ["a", "gold", "b"]
    gold = ["gold"]
    assert mrr_at_k(ranked_links, gold, k=5) == pytest.approx(0.5)


def test_ndcg_at_k_basic():
    # ideal has gold at top
    ranked = ["gold", "a", "b"]
    gold = ["gold"]
    score = ndcg_at_k(ranked, gold, k=3)
    assert score > 0.99


def test_top_k_domain_hit():
    ranked = ["https://ex.com/a", "https://gold.com/b"]
    gold = ["https://gold.com/b"]
    assert top_k_domain_hit(ranked, gold, k=2) == 1.0


def test_metrics_on_fixture_candidates():
    fixture = Path("tests/fixtures/rerank_candidates.json")
    data = json.loads(fixture.read_text())
    for case in data:
        ranked = [c["link"] for c in sorted(case["candidates"], key=lambda x: -x.get("score", 0))]
        g = case["gold_links"]
        mrr = mrr_at_k(ranked, g, k=5)
        nd = ndcg_at_k(ranked, g, k=5)
        assert 0.0 <= mrr <= 1.0
        assert 0.0 <= nd <= 1.0


def test_provider_survival_and_counts():
    # simple survival stub using before/after
    before = 20
    after = 12
    surv = provider_survival_rate(before, after)
    assert surv == pytest.approx(0.6)
