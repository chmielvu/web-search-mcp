from __future__ import annotations

import asyncio
from pathlib import Path

from scripts.rerank_eval_common import load_jsonl
from scripts.rerank_prompt_eval import (
    _baseline_rankllm_candidate,
    _candidate,
    _proposed_rankllm_candidate,
    baseline_cross_query,
    baseline_rankllm_query,
    profile_contract,
    run_replay,
    validate_quality_fixture,
)


FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/web_rerank_quality_cases.jsonl"


def test_quality_fixture_has_six_intents_and_32_candidates() -> None:
    records = load_jsonl(FIXTURE)
    summary = validate_quality_fixture(records)
    assert summary["row_count"] == 36
    assert summary["rows_per_intent"] == {
        "ai_coding_and_infrastructure": 6,
        "comparison": 6,
        "digital_humanities": 6,
        "general": 6,
        "news": 6,
        "social_media": 6,
    }
    assert all(len(record["candidates"]) == 32 for record in records)


def test_baseline_profile_reproduces_old_prompt_shapes() -> None:
    assert baseline_cross_query(" query ", "comparison", "goal") == (
        "query | Prefer primary sources, original documentation, and in-depth content. "
        "Demote SEO listicles, aggregator pages, and ads-heavy sites. | Research goal: goal"
    )
    assert baseline_rankllm_query("query", "Prefer GitHub.") == (
        "query | Caller reranking instructions: Prefer GitHub."
    )
    candidate = _candidate(load_jsonl(FIXTURE)[0]["candidates"][0])
    assert set(_baseline_rankllm_candidate(candidate)) == {"title", "content"}
    assert "Domain:" not in _baseline_rankllm_candidate(candidate)["content"]


def test_proposed_profile_exposes_only_six_rankllm_fields() -> None:
    candidate = _candidate(load_jsonl(FIXTURE)[0]["candidates"][0])
    content = _proposed_rankllm_candidate(candidate)["content"]
    assert content.splitlines()[-6:] == [
        f"Title: {candidate.title}",
        f"Snippet: {candidate.snippet}",
        f"URL: {candidate.link}",
        f"Domain: {candidate.domain}",
        "Providers: fixture-primary",
        "ProviderCount: 2",
    ]
    assert profile_contract("proposed")["rankllm_candidate_fields"] == [
        "Title",
        "Snippet",
        "URL",
        "Domain",
        "Providers",
        "ProviderCount",
    ]


def test_offline_replay_writes_all_requested_sections() -> None:
    records = load_jsonl(FIXTURE)
    report = asyncio.run(
        run_replay(
            records,
            profiles=["baseline", "proposed"],
            stages=["cross", "rankllm", "pipeline"],
            order_check_cases=6,
            repetitions=1,
            offline=True,
        )
    )
    assert set(report["stages"]) == {"cross", "rankllm", "pipeline"}
    assert set(report["stages"]["pipeline"]) == {"baseline", "proposed", "pair_validity"}
    assert report["stages"]["pipeline"]["pair_validity"]["all_valid"] is True
    assert len(report["stages"]["cross"]["proposed"]["cases"]) == 36
    assert len(report["order_check"]["proposed"]["rows"]) == 6
    assert set(report["fixture"]["rows_per_intent"]) == {
        "general",
        "comparison",
        "social_media",
        "ai_coding_and_infrastructure",
        "digital_humanities",
        "news",
    }
