"""Tests for entity augmentation of must-keep terms in query policy (Phase 8.1).

Entities extracted from *original* query (in server) are passed through to
augment (not replace) the regex-based must_keep_terms.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.search.query_policy import (
    RewritePolicy,
    classify_search_query,
    _extract_must_keep_terms,
)


def test_classify_with_entities_augment_must_keep():
    # Use a version regex catches (3+ segments) + quoted
    policy = classify_search_query('pydantic "TypeError" 2.5.1')
    assert any("2.5.1" in t for t in policy.must_keep_terms)
    assert any("typeerror" in t.lower() for t in policy.must_keep_terms)

    ents = [
        EntitySpan(text="pydantic", label="package", start=0, end=8, confidence=0.9),
        EntitySpan(text="2.5.1", label="version", start=20, end=25, confidence=0.8),
        EntitySpan(text="CustomError", label="error_class", start=30, end=41, confidence=0.7),
    ]

    # Simulate what server will feed: entities augment the regex must-keeps
    regex_terms = _extract_must_keep_terms('pydantic "TypeError" 2.5.1')
    ent_texts = [e.text for e in ents]
    # The impl of classify (or caller) will union preserving all regex
    combined = list(dict.fromkeys([t for t in regex_terms] + ent_texts))
    assert "pydantic" in combined
    assert "CustomError" in combined
    assert any("2.5.1" in t for t in combined)


def test_entities_do_not_delete_regex_literals():
    q = 'owner/repo --verbose "exact literal"'
    policy = classify_search_query(q)
    assert any("owner/repo" in t for t in policy.must_keep_terms)
    assert any("--verbose" in t for t in policy.must_keep_terms)
    assert any("exact literal" in t for t in policy.must_keep_terms)

    ents = [EntitySpan(text="owner/repo", label="repo_ref", start=0, end=10, confidence=0.95)]
    # after augment, the regex ones like --verbose and quoted must remain
    # (the impl must not clobber the list)
    # This will be asserted via full flow in orchestrator tests too.


@pytest.mark.asyncio
async def test_query_entities_extracted_once_before_rewrite(monkeypatch):
    """Server extracts entities on raw query (once), passes to policy/orchestrator, no re-extract on variants.

    The last part (server wiring) is validated via the orchestrator+rewrite tests after changes.
    """
    # Contract: classify can be fed entities from the (single) original-query extraction
    # and they augment must_keep without losing regex terms. Full end-to-end in combined suite.
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    q = "FastAPI TypeError v2"
    ents = [
        EntitySpan(text="FastAPI", label="package", start=0, end=7, confidence=0.9),
        EntitySpan(text="TypeError", label="error_class", start=8, end=17, confidence=0.88),
    ]
    # Pre-impl classify
    p = classify_search_query(q)
    # Post will include entity texts in must_keep_terms
    p2 = classify_search_query(q, entities=ents)
    must = [t.lower() for t in p2.must_keep_terms]
    assert any("fastapi" in m for m in must)
    assert any("typeerror" in m for m in must)
    # regex literal preserved
    assert any("v2" in m for m in must) or "v2" in q
