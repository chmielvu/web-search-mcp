"""Tests for deterministic Agent-Ready Evidence Rate and candidate evaluation metrics."""

from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.evals.metrics import (
    agent_ready_breakdown,
    agent_ready_evidence_rate,
    assess_candidate_readiness,
    candidate_failure_reasons,
    evidence_rate,
    is_candidate_agent_ready,
    line_precision_rate,
    match_data_rate,
)


def _make_ready_code_candidate() -> dict[str, object]:
    return {
        "result_kind": "code_match",
        "url": "https://github.com/astral-sh/uv/blob/0.2.20/crates/uv-resolver/src/resolver/mod.rs#L85-L140",
        "path": "crates/uv-resolver/src/resolver/mod.rs",
        "repository": "astral-sh/uv",
        "line_start": 85,
        "line_end": 140,
        "commit_oid": "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1",
        "snippet": "pub struct Resolver<'a> { ... }",
        "hydrated_source": "pub struct Resolver<'a> {\n    requirements: &'a [Requirement],\n}",
        "location": {
            "url": "https://github.com/astral-sh/uv/blob/0.2.20/crates/uv-resolver/src/resolver/mod.rs#L85-L140",
            "path": "crates/uv-resolver/src/resolver/mod.rs",
            "line_start": 85,
            "line_end": 140,
            "revision": "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1",
            "lines_available": True,
            "revision_available": True,
            "precision": "line",
        },
    }


def _make_ready_doc_candidate() -> dict[str, object]:
    return {
        "result_kind": "documentation",
        "url": "https://duckdb.org/docs/api/python/overview",
        "path": "docs/api/python/overview.md",
        "line_start": 1,
        "line_end": 50,
        "revision": "v1.0.0",
        "snippet": "DuckDB Python API Overview guide and reference.",
        "location": {
            "url": "https://duckdb.org/docs/api/python/overview",
            "line_start": 1,
            "line_end": 50,
            "revision": "v1.0.0",
            "lines_available": True,
            "revision_available": True,
        },
    }


def test_perfect_evidence_rate() -> None:
    candidates = [
        _make_ready_code_candidate(),
        _make_ready_doc_candidate(),
    ]
    rate = agent_ready_evidence_rate(candidates)
    assert rate == pytest.approx(1.0)
    assert evidence_rate(candidates) == pytest.approx(1.0)


def test_partial_evidence_rate() -> None:
    ready_cand = _make_ready_code_candidate()
    unready_cand = {
        "result_kind": "code_match",
        # Missing URL
        "line_start": 10,
        "snippet": "def foo(): pass",
    }
    candidates = [ready_cand, unready_cand]
    rate = agent_ready_evidence_rate(candidates)
    assert rate == pytest.approx(0.5)


def test_empty_candidates_returns_zero() -> None:
    assert agent_ready_evidence_rate([]) == 0.0
    assert agent_ready_evidence_rate(None) == 0.0
    assert evidence_rate([]) == 0.0


def test_semantic_only_candidates_do_not_count() -> None:
    semantic_cand_1 = {
        "result_kind": "semantic_page",
        "url": "https://example.com/blog/article",
        "snippet": "A general discussion about python async patterns.",
        "line_start": 1,
        "revision": "main",
    }
    semantic_cand_2 = {
        "result_kind": "semantic",
        "url": "https://example.com/topic/guide",
        "snippet": "Semantic overview without code anchor.",
        "commit_oid": "abcdef123456",
    }
    candidates = [semantic_cand_1, semantic_cand_2]
    rate = agent_ready_evidence_rate(candidates)
    assert rate == 0.0

    ready, reasons = assess_candidate_readiness(semantic_cand_1)
    assert not ready
    assert "non_evidence_result_kind" in reasons


def test_repository_only_candidates_do_not_count() -> None:
    repo_cand = {
        "result_kind": "repository",
        "url": "https://github.com/astral-sh/uv",
        "repository": "astral-sh/uv",
        "snippet": "Fast Python package installer and resolver.",
    }
    candidates = [repo_cand]
    assert agent_ready_evidence_rate(candidates) == 0.0

    ready, reasons = assess_candidate_readiness(repo_cand)
    assert not ready
    assert "non_evidence_result_kind" in reasons


def test_candidate_missing_url_fails() -> None:
    cand = {
        "result_kind": "code_match",
        "line_start": 10,
        "commit_oid": "abc1234",
        "snippet": "x = 42",
    }
    ready, reasons = assess_candidate_readiness(cand)
    assert not ready
    assert "missing_url" in reasons
    assert not is_candidate_agent_ready(cand)
    assert "missing_url" in candidate_failure_reasons(cand)


def test_candidate_missing_text_context_fails() -> None:
    cand = {
        "result_kind": "code_match",
        "url": "https://github.com/org/repo/blob/main/src/lib.rs#L10",
        "line_start": 10,
        "commit_oid": "abc1234",
        # snippet, hydrated_source, fragments all absent/empty
    }
    ready, reasons = assess_candidate_readiness(cand)
    assert not ready
    assert "insufficient_text_context" in reasons


def test_candidate_missing_lines_and_revision_fails() -> None:
    cand = {
        "result_kind": "code_match",
        "url": "https://github.com/org/repo/blob/main/src/lib.rs",
        "snippet": "pub fn hello() {}",
        # line_start is None, revision/commit_oid is None
    }
    ready, reasons = assess_candidate_readiness(cand)
    assert not ready
    assert "missing_lines_or_revision" in reasons


def test_candidate_with_hydrated_source_and_lines_succeeds() -> None:
    cand = {
        "result_kind": "code_match",
        "url": "https://github.com/org/repo/blob/main/src/lib.rs#L20-L30",
        "line_start": 20,
        "line_end": 30,
        "hydrated_source": "pub fn solve() -> bool {\n    true\n}",
    }
    ready, reasons = assess_candidate_readiness(cand)
    assert ready
    assert reasons == []
    assert is_candidate_agent_ready(cand)


def test_candidate_with_revision_and_snippet_succeeds() -> None:
    cand = {
        "result_kind": "documentation",
        "url": "https://example.com/docs/intro",
        "revision": "a" * 40,
        "snippet": "Introductory guide to the API client library.",
    }
    ready, reasons = assess_candidate_readiness(cand)
    assert ready
    assert reasons == []


def test_agent_ready_breakdown() -> None:
    ready_1 = _make_ready_code_candidate()
    ready_2 = _make_ready_doc_candidate()
    unready = {
        "result_kind": "semantic_page",
        "url": "https://example.com/blog",
        "snippet": "Some blog content.",
    }

    breakdown = agent_ready_breakdown([ready_1, unready, ready_2])
    assert breakdown["total"] == 3
    assert breakdown["ready_count"] == 2
    assert breakdown["evidence_rate"] == pytest.approx(2 / 3)
    assert breakdown["ready_indices"] == [0, 2]
    assert len(breakdown["failures"]) == 1
    assert breakdown["failures"][0]["index"] == 1
    assert "non_evidence_result_kind" in breakdown["failures"][0]["reasons"]

    # Empty breakdown
    empty_bd = agent_ready_breakdown([])
    assert empty_bd["total"] == 0
    assert empty_bd["ready_count"] == 0
    assert empty_bd["evidence_rate"] == 0.0
    assert empty_bd["failures"] == []


def test_line_precision_rate() -> None:
    cands = [
        {"url": "https://example.com/a", "line_start": 10},
        {"url": "https://example.com/b", "lines_available": True},
        {"url": "https://example.com/c"},  # No lines
    ]
    assert line_precision_rate(cands) == pytest.approx(2 / 3)
    assert line_precision_rate([]) == 0.0


def test_match_data_rate() -> None:
    cands = [
        {"url": "https://example.com/a", "match_data_available": True},
        {"url": "https://example.com/b", "fragments": [{"text": "match"}]},
        {"url": "https://example.com/c"},  # No match data
    ]
    assert match_data_rate(cands) == pytest.approx(2 / 3)
    assert match_data_rate([]) == 0.0
