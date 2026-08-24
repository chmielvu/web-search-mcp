"""Tests for heuristics/shaping.py role-dialect cascade."""

from __future__ import annotations

import time

from kindly_web_search_mcp_server.heuristics.query_features import build_query_features
from kindly_web_search_mcp_server.heuristics.shaping import (
    AugmentResult,
    extract_search_ops,
    shape_for_branch,
)


def _feat(query: str, **kwargs):
    return build_query_features(query, **kwargs)


class TestExtractSearchOps:
    def test_offsets_reproduce_surface(self) -> None:
        q = 'docker "exact phrase" site:docs.docker.com -bad intitle:x'
        ops = extract_search_ops(q)
        assert ops.spans
        for span in ops.spans:
            assert q[span.start : span.end] == span.text

    def test_classes(self) -> None:
        ops = extract_search_ops('a "p h" site:x.com filetype:pdf -term lang:fr +plus AND')
        assert ops.phrases[0].value == "p h"
        assert ops.sites[0].value == "x.com"
        assert ops.filetypes[0].value == "pdf"
        assert ops.excludes[0].value == "term"
        assert len(ops.engine_only) == 3

    def test_near_miss_invalid_payloads_dropped(self) -> None:
        ops = extract_search_ops('x site:nodots filetype:"pdf"')
        assert ops.sites == ()
        assert ops.filetypes == ()

    def test_exclude_with_structured_prefix_swallows_inner_candidate(self) -> None:
        ops = extract_search_ops("docs -site:spam.example.com end")
        assert len(ops.excludes) == 1
        assert ops.sites == ()

    def test_empty(self) -> None:
        assert extract_search_ops("").spans == ()
        assert extract_search_ops("   ").truncated is False


class TestShapeForBranchFree:
    def test_strips_all_but_phrases(self) -> None:
        q = 'fastapi deploy guide site:x.com filetype:pdf -slow lang:en "async pool"'
        out = shape_for_branch("free", q, _feat(q))
        assert isinstance(out, AugmentResult)
        assert "site:" not in out.query and "filetype:" not in out.query
        assert "-slow" not in out.query and "lang:" not in out.query
        assert '"async pool"' in out.query
        assert "strip.ops" in out.rules_applied

    def test_word_budget_trim(self) -> None:
        q = " ".join(f"w{i}" for i in range(20))
        out = shape_for_branch("free", q, _feat(q))
        assert len(out.query.split()) <= 12
        assert "budget.trim" in out.rules_applied

    def test_segment_glued_repair(self) -> None:
        q = "toplawyersinnewyork"
        out = shape_for_branch("free", q, _feat(q))
        assert out.query == "top lawyers in new york"
        assert "segment.glued" in out.rules_applied


class TestShapeForBranchSerp:
    def test_serp_keeps_lcd_strips_engine(self) -> None:
        q = 'pydantic v2 site:docs.pydantic.dev intitle:guide lang:en -strict "model config"'
        out = shape_for_branch("serp2", q, _feat(q))
        assert "site:docs.pydantic.dev" in out.query
        assert '"model config"' in out.query
        assert "-strict" in out.query
        assert "intitle:" not in out.query and "lang:" not in out.query

    def test_serp1_char_cap(self) -> None:
        q = "x" * 500
        out = shape_for_branch("serp1", q, _feat(q))
        assert len(out.query) <= 400

    def test_confounder_op_inside_quoted_phrase_not_stripped(self) -> None:
        q = 'compare "site: docs style" guides'
        out = shape_for_branch("serp2", q, _feat(q))
        assert '"site: docs style"' in out.query

    def test_preserved_term_blocks_strip(self) -> None:
        understanding = type(
            "U",
            (),
            {
                "intent": "general",
                "preserved_terms": ["lang:python"],
                "domain_hints": [],
            },
        )()
        feats = _feat("bench lang:python speed", understanding=understanding)
        out = shape_for_branch("serp2", "bench lang:python speed", feats)
        assert "lang:python" in out.query


class TestShapeForBranchSemantic:
    def test_strips_everything_and_unquotes(self) -> None:
        q = 'which db "row level security" site:x.com -slow lang:en'
        out = shape_for_branch("semantic_tavily", q, _feat(q))
        assert '"' not in out.query
        assert "site:" not in out.query and "-" not in out.query and "lang:" not in out.query


class TestLangGate:
    def test_non_english_skips_surgery(self) -> None:
        q = "najlepsza biblioteka do grafów site:x.com"
        feats = _feat(q)
        if feats.lang != "pl":  # lingua must detect; guard against env drift
            return
        out = shape_for_branch("serp2", q, feats)
        assert out.rules_applied == ("skip.non_english",)


class TestDeterminismAndPerf:
    def test_deterministic(self) -> None:
        q = 'a site:x.com -b "c d" intitle:e'
        f = _feat(q)
        assert shape_for_branch("serp2", q, f) == shape_for_branch("serp2", q, f)

    def test_long_input_failure_perf(self) -> None:
        q = ("lorem ipsum dolor sit amet consectetur " * 400)[:10_000]
        start = time.monotonic()
        out = shape_for_branch("free", q, _feat(q))
        elapsed = time.monotonic() - start
        assert elapsed < 2.0
        assert out.query

    def test_original_role_passthrough_additive_only(self) -> None:
        q = "keep site:this.one intact"
        out = shape_for_branch("original", q, _feat(q))
        assert out.query == q
