"""Tests for entity overlap as rerank feature (Phase 8.3).

Score in [-1.0, 1.0]. Blend only under KINDLY_RERANK_ENTITY_OVERLAP_ENABLED.
Penalties for version/repo mismatch; boost for exact package etc.
"""

from __future__ import annotations


from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.entity.overlap import compute_entity_overlap


def test_exact_package_match_positive():
    q_ents = [EntitySpan(text="FastAPI", label="package", start=0, end=7, confidence=0.9)]
    c_ents = [{"text": "FastAPI", "label": "package", "confidence": 0.95}]
    score = compute_entity_overlap(q_ents, c_ents)
    assert score > 0.2


def test_version_mismatch_penalty():
    q_ents = [
        EntitySpan(text="FastAPI", label="package", start=0, end=7),
        EntitySpan(text="2.5.1", label="version", start=8, end=13),
    ]
    c_ents = [
        {"text": "FastAPI", "label": "package"},
        {"text": "3.0.0", "label": "version"},
    ]
    score = compute_entity_overlap(q_ents, c_ents)
    assert score < 0.0  # penalty


def test_repo_mismatch_penalty():
    q = [EntitySpan(text="owner/repo", label="repo_ref", start=0, end=10)]
    c = [{"text": "other/project", "label": "repo_ref"}]
    score = compute_entity_overlap(q, c)
    assert score < 0


def test_neutral_labels():
    q = [EntitySpan(text="foo", label="api_function", start=0, end=3)]
    c = [{"text": "bar", "label": "api_function"}]
    score = compute_entity_overlap(q, c)
    # neutral ~0
    assert -0.1 <= score <= 0.1


def test_bounded_range():
    q = [EntitySpan(text="p", label="package")]
    c = [{"text": "p", "label": "package"}]
    for _ in range(3):
        s = compute_entity_overlap(q, c)
        assert -1.0 <= s <= 1.0
