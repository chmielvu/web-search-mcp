"""Tests for code_search_golden.jsonl fixture and EvalCase round-tripping."""

from __future__ import annotations

import json
from pathlib import Path

from kindly_web_search_mcp_server.evals.cases import (
    EvalCase,
    load_eval_cases,
    load_eval_cases_from_jsonl,
    save_eval_cases_to_jsonl,
)
from kindly_web_search_mcp_server.evals.metrics import agent_ready_evidence_rate


def _fixture_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "evals" / "code_search_golden.jsonl"


def test_fixture_file_exists() -> None:
    path = _fixture_path()
    assert path.exists(), f"Expected fixture file at {path}"


def test_load_golden_cases_count() -> None:
    cases = load_eval_cases_from_jsonl(_fixture_path())
    assert 10 <= len(cases) <= 25
    assert len(cases) == 14

    # Alias check
    cases_alias = load_eval_cases(_fixture_path())
    assert len(cases_alias) == len(cases)


def test_golden_cases_category_coverage() -> None:
    cases = load_eval_cases_from_jsonl(_fixture_path())
    expected_categories = {
        "symbol_lookup",
        "regex",
        "repo_scope",
        "discovery",
        "docs",
        "error_fix",
        "path_language",
    }
    present_categories = {c.category for c in cases if c.category is not None}
    assert expected_categories.issubset(present_categories)
    assert expected_categories == present_categories


def test_golden_cases_fields_valid() -> None:
    cases = load_eval_cases_from_jsonl(_fixture_path())
    seen_ids = set()
    for case in cases:
        assert case.eval_case_id, "eval_case_id must be non-empty"
        assert case.eval_case_id not in seen_ids, f"Duplicate ID: {case.eval_case_id}"
        seen_ids.add(case.eval_case_id)

        assert case.suite_name == "code_search_golden"
        assert case.query.strip(), "query must be non-empty"
        assert case.research_goal is not None
        assert case.category is not None
        assert case.expected_kind in {"code_match", "documentation"}
        assert len(case.expected_tool_calls) > 0
        assert case.expected_tool_calls[0].tool_name == "code_search"
        assert len(case.candidate_sets) > 0
        assert len(case.candidate_sets[0].candidates) > 0


def test_eval_case_jsonl_roundtrip(tmp_path: Path) -> None:
    original_cases = load_eval_cases_from_jsonl(_fixture_path())

    temp_file = tmp_path / "roundtrip.jsonl"
    save_eval_cases_to_jsonl(original_cases, temp_file)

    reloaded_cases = load_eval_cases_from_jsonl(temp_file)
    assert len(reloaded_cases) == len(original_cases)

    for orig, reloaded in zip(original_cases, reloaded_cases, strict=True):
        assert orig.eval_case_id == reloaded.eval_case_id
        assert orig.suite_name == reloaded.suite_name
        assert orig.query == reloaded.query
        assert orig.category == reloaded.category
        assert orig.research_goal == reloaded.research_goal
        assert orig.expected_repo == reloaded.expected_repo
        assert orig.expected_path_substring == reloaded.expected_path_substring
        assert orig.expected_kind == reloaded.expected_kind
        assert orig.expected_line_span == reloaded.expected_line_span
        assert orig.gold_urls == reloaded.gold_urls
        assert len(orig.candidate_sets) == len(reloaded.candidate_sets)
        assert len(orig.candidate_sets[0].candidates) == len(reloaded.candidate_sets[0].candidates)


def test_golden_cases_evidence_rate_on_candidates() -> None:
    cases = load_eval_cases_from_jsonl(_fixture_path())
    for case in cases:
        for cset in case.candidate_sets:
            rate = agent_ready_evidence_rate(cset.candidates)
            # Golden fixture candidates should all be agent-ready
            assert rate == 1.0, f"Case {case.eval_case_id} had evidence rate {rate}"


def test_eval_case_model_validation_direct() -> None:
    raw = {
        "id": "test-case-custom",
        "suite_name": "custom_suite",
        "query": "find custom helper",
        "category": "symbol_lookup",
        "custom_extra_attribute": "extra_value",
    }
    case = EvalCase.model_validate(raw)
    assert case.eval_case_id == "test-case-custom"
    assert case.suite_name == "custom_suite"
    assert case.category == "symbol_lookup"
    assert getattr(case, "custom_extra_attribute", None) == "extra_value"

    dumped = json.loads(case.model_dump_json())
    assert dumped["eval_case_id"] == "test-case-custom"
    assert dumped["custom_extra_attribute"] == "extra_value"
