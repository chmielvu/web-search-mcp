"""Tests for evals/judges.py JSON-only parsing and metric surface (no LLM calls in unit tests)."""

from __future__ import annotations

import json



def test_strict_json_parser_handles_pure_and_fenced():
    from kindly_web_search_mcp_server.evals.judges import _parse_strict_json

    pure = '{"score": 0.9, "reason": "good"}'
    assert _parse_strict_json(pure)["score"] == 0.9

    fenced = '```json\n{"score": 0.75, "reason": "ok"}\n```'
    assert _parse_strict_json(fenced)["score"] == 0.75

    bad = 'Here is the score: {"score": 0.1}'
    # should still extract
    assert _parse_strict_json(bad)["score"] == 0.1


def test_judge_fns_return_score_and_persist_structure(monkeypatch):
    # Patch the llm call and persist to avoid real net/db in unit test
    from kindly_web_search_mcp_server.evals import judges as jmod

    calls = []

    def fake_llm(prompt: str) -> str:
        return json.dumps({"score": 0.82, "reason": "parsed from unit test"})

    def fake_persist(*a, **k):
        calls.append((a, k))

    monkeypatch.setattr(jmod, "_call_judge_llm", fake_llm)
    monkeypatch.setattr(jmod, "_persist_judge_call", fake_persist)

    res = jmod.judge_tool_choice_correct(
        "query about fastmcp",
        [{"tool_name": "web_search"}],
        [{"tool_name": "web_search", "required": True}],
        eval_run_id="r1",
        eval_case_id="c1",
        run_key="rk1",
        persist=True,
    )
    assert res["score"] == 0.82
    assert len(calls) == 1

    res2 = jmod.judge_argument_correctness("q", [], eval_run_id="r1", eval_case_id="c1", run_key="rk1")
    assert "score" in res2

    res3 = jmod.judge_source_usefulness("q", [{"title": "t"}], eval_run_id="r1", eval_case_id="c1", run_key="rk1")
    assert res3["score"] == 0.82

    res4 = jmod.judge_ranking_quality("q", ["u1"], ["u1"], eval_run_id="r1", eval_case_id="c1", run_key="rk1")
    assert res4["score"] == 0.82


def test_judge_metrics_are_the_four_listed():
    # Just surface check the public API
    from kindly_web_search_mcp_server.evals.judges import (
        judge_argument_correctness,
        judge_ranking_quality,
        judge_source_usefulness,
        judge_tool_choice_correct,
    )

    assert all(
        callable(x)
        for x in [
            judge_tool_choice_correct,
            judge_argument_correctness,
            judge_source_usefulness,
            judge_ranking_quality,
        ]
    )
