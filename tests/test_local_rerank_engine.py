"""Local rerank baseline engine tests (Phase 4.2)."""

from __future__ import annotations

from unittest.mock import patch

import asyncio

from kindly_web_search_mcp_server.rerank.engines import (
    LocalBaselineRerankEngine,
    get_rerank_engine,
)
from kindly_web_search_mcp_server.rerank.models import RerankCandidate


def test_local_baseline_registered():
    eng = get_rerank_engine("local_baseline")
    assert eng.engine_id == "local_baseline"
    assert isinstance(eng, LocalBaselineRerankEngine)


def test_local_baseline_returns_empty_when_flashrank_missing():
    # flashrank not installed in test venv -> engine returns [] to preserve order (none behavior)
    eng = LocalBaselineRerankEngine()
    rc = [RerankCandidate(index=0, document="d0"), RerankCandidate(index=1, document="d1")]
    res = asyncio.run(eng.rerank("q", rc))
    assert res == []


def test_local_baseline_reorders_with_mocked_flashrank(monkeypatch):
    fake_results = [
        {"id": 1, "score": 0.95, "meta": {"index": 1}},
        {"id": 0, "score": 0.42, "meta": {"index": 0}},
    ]

    class FakeRanker:
        def __init__(self, *a, **k): pass
        def rerank(self, req):
            return fake_results

    class FakeReq:
        def __init__(self, **k): pass

    import sys as _sys
    fake_mod = type(_sys)("flashrank")
    fake_mod.Ranker = FakeRanker
    fake_mod.RerankRequest = FakeReq
    monkeypatch.setitem(_sys.modules, "flashrank", fake_mod)

    eng = LocalBaselineRerankEngine()
    rc = [RerankCandidate(index=0, document="t0 s0"), RerankCandidate(index=1, document="t1 s1")]
    res = asyncio.run(eng.rerank("query", rc))
    assert len(res) == 2
    assert [r.index for r in res] == [1, 0]
    assert res[0].score > res[1].score
