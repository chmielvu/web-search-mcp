"""Named-slot rewrite contract tests (replaces positional 5-string schema)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from kindly_web_search_mcp_server.heuristics.query_features import build_query_features
from kindly_web_search_mcp_server.prompts.query_rewrite import (
    REWRITE_PROMPT_VERSION,
    REWRITE_SYSTEM,
    REWRITE_USER,
    RewrittenQueries,
)
from kindly_web_search_mcp_server.search.intents import SearchIntent
from kindly_web_search_mcp_server.search.planning import (
    SLOT_ORDER,
    _REWRITE_CACHE,
    _branch_fallback_queries,
    _rewrite_queries,
)


def test_prompt_version_is_string() -> None:
    assert isinstance(REWRITE_PROMPT_VERSION, str) and REWRITE_PROMPT_VERSION


def test_user_template_has_named_slots_and_no_specialized_block() -> None:
    for token in (
        "<FREE_QUERY_RULES>",
        "<SERP_QUERY_RULES>",
        "<TAVILY_QUERY_RULES>",
        "<EXA_QUERY_RULES>",
        "{compared_entities}",
        "{time_sensitivity}",
        "{should_decompose}",
        "{preserved_terms}",
    ):
        assert token in REWRITE_USER
    assert "<SPECIALIZED_QUERY_RULES>" not in REWRITE_USER
    assert "{specialized_guidance}" not in REWRITE_USER


def test_no_stale_specialized_provider_strings_in_prompts() -> None:
    for banned in ("Hacker News", "Reddit", "Telegram", "code_search tool"):
        assert banned not in REWRITE_USER
        assert banned not in REWRITE_SYSTEM


def test_serp_rules_enforce_lcd_only() -> None:
    assert "site:" in REWRITE_USER and "-term" in REWRITE_USER
    for banned_op in ("intitle:", "inbody:", "inpage:", "lang:", "+term"):
        idx = REWRITE_USER.find(banned_op)
        # banned operators may only appear in the Forbidden enumeration line
        if idx != -1:
            line = REWRITE_USER[max(0, idx - 60) : idx].rsplit("\n", 1)[-1]
            assert "Forbidden" in line or "ONLY" in REWRITE_USER[max(0, idx - 200) : idx]


def test_named_model_parses_and_rejects_extras() -> None:
    payload = '{"free":"a","serp1":"b","serp2":"c","semantic_tavily":"d","semantic_exa":"e"}'
    parsed = RewrittenQueries.model_validate_json(payload)
    assert parsed.free == "a" and parsed.semantic_exa == "e"
    with pytest.raises(ValidationError):
        RewrittenQueries.model_validate_json('{"queries": ["a","b","c","d","e"]}')
    with pytest.raises(ValidationError):
        RewrittenQueries.model_validate_json(
            '{"free":"a","serp1":"b","serp2":"c","semantic_tavily":"d"}'
        )


def _features(query: str, **understanding_fields):
    fields = dict(
        intent="general",
        preserved_terms=[],
        domain_hints=[],
        compared_entities=[],
        time_sensitivity="none",
        should_decompose=False,
    )
    fields.update(understanding_fields)
    understanding = SimpleNamespace(**fields)
    return build_query_features(query, understanding=understanding, support_terms=("kw1",))


def test_free_fallback_prefers_segmented_variant() -> None:
    feats = _features("toplawyersinnewyork")
    assert feats.segmented_variants
    fb = _branch_fallback_queries(
        feats, terms=(), suggestions=(), research_goal="find one", current_year="2026"
    )
    assert fb[0].startswith("top lawyers in new york")


def test_time_gated_year_suffix() -> None:
    base = _features("rust async runtime")
    cold = _branch_fallback_queries(
        base, terms=(), suggestions=(), research_goal="", current_year="2026"
    )
    hot_feats = _features("rust async runtime", time_sensitivity="recent")
    hot = _branch_fallback_queries(
        hot_feats, terms=(), suggestions=(), research_goal="", current_year="2026"
    )
    assert "2026" not in cold[0]
    assert "2026" in hot[0]


def test_serp2_uses_compared_facet_and_diverges_from_serp1() -> None:
    feats = _features(
        "fastapi vs starlette performance", compared_entities=["FastAPI", "Starlette"]
    )
    fb = _branch_fallback_queries(
        feats, terms=("kw1",), suggestions=(), research_goal="pick one", current_year="2026"
    )
    assert "FastAPI vs Starlette" in fb[2]
    assert fb[2] != fb[1]
    assert "authoritative sources" in fb[4]
    assert fb[3].endswith("pick one") or "pick one" in fb[3]


def test_slot_order_matches_persistence_contract() -> None:
    assert SLOT_ORDER == ("free", "serp1", "serp2", "semantic_tavily", "semantic_exa")


def test_rewrite_cache_key_includes_intent() -> None:
    """Identical rewrite inputs with different intents must not share a cache entry."""
    _REWRITE_CACHE.clear()
    payload = '{"free":"a","serp1":"b","serp2":"c","semantic_tavily":"d","semantic_exa":"e"}'
    calls: list[dict] = []

    def run_rewrite(intent: SearchIntent) -> None:
        asyncio.run(
            _rewrite_queries(
                intent=intent,
                query="fastapi vs starlette performance",
                research_goal="pick one",
                terms=("framework",),
                suggestions=(),
                current_year="2026",
                understanding=SimpleNamespace(
                    compared_entities=["FastAPI", "Starlette"],
                    preserved_terms=[],
                    time_sensitivity="none",
                    should_decompose=True,
                ),
            )
        )

    async def fake_complete_json(**kw: object) -> SimpleNamespace:
        calls.append(kw)
        return SimpleNamespace(
            content=payload,
            model_used="test-model",
            input_tokens=10,
            output_tokens=10,
        )

    with patch(
        "kindly_web_search_mcp_server.search.planning.build_worker_router"
    ) as router_factory:
        router_factory.return_value.complete_json = AsyncMock(side_effect=fake_complete_json)
        run_rewrite("general")
        run_rewrite("comparison")
        run_rewrite("comparison")  # cache hit
    assert len(calls) == 2, "different intents must not reuse the rewrite cache"
    _REWRITE_CACHE.clear()
