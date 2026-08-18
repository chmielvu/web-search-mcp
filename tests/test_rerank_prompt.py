from typing import cast

import pytest

from kindly_web_search_mcp_server.prompts.rerank import (
    RERANK_INTENT_INSTRUCTIONS,
    SHARED_RANKING_INSTRUCTIONS,
    build_cross_encoder_query,
    build_rankllm_query,
    build_relevance_query,
)
from kindly_web_search_mcp_server.search.intents import SearchIntent, normalize_intent


EXPECTED_INTENTS = {
    "general": """For factual questions, prefer direct primary or official evidence when
the candidate visibly provides it.

For reviews, comparisons, user experiences, public opinion, or discovery,
prefer relevant independent and community sources that provide the requested
perspective. Do not demote a source merely because it is secondary.

Demote SEO listicles, scraped mirrors, ad-first pages, and unsupported
opinion when they do not provide direct evidence.""",
    "comparison": """Identify every compared subject and requested criterion from the query.

Rank candidates covering all named subjects with comparable criteria,
evidence, measurements, or methodology first.

A source covering only one side is supporting evidence, not a complete
comparison. Do not rank a one-sided official page above a balanced,
directly relevant comparison merely because it is official.""",
    "social_media": """Match the requested platform or community using the visible URL, Domain,
Title, and Snippet.

For questions about user experience, discussion, sentiment, or community
practice, rank relevant platform-native posts and threads first.

For factual announcements, rank official account or platform sources first.

Treat anecdotes as weaker than corroborated evidence. Demote SEO rewrites,
cross-post mirrors, and summaries that do not link to the underlying
discussion.""",
    "ai_coding_and_infrastructure": """Match the requested framework, language, package, API, and version from the
visible query and candidate text.

Prefer official documentation, specifications, source repositories, and
relevant GitHub issues or pull requests for the requested technology.

A technically direct issue or version-matched document outranks a generic
tutorial for another version. Demote SEO tool lists and content-farm
tutorials.""",
    "digital_humanities": """Match the requested period, corpus, method, and discipline.

Prefer primary scholarly material, peer-reviewed research, recognized
preprints, and methodologically explicit surveys.

Demote uncited blogs, vendor material, and generic commentary unless the
query asks specifically for public or industry commentary.""",
    "news": """Rank candidates by direct relevance to the named event, person, organization,
or development.

Use only visible Title, Snippet, URL, Domain, Providers, and ProviderCount.
Do not infer publication order or freshness from a URL, provider name, or
writing style.

The news provider policy handles the available freshness filtering upstream.
The reranker must not claim that one candidate is newer without visible
date evidence.""",
}


@pytest.mark.parametrize("intent", EXPECTED_INTENTS)
def test_intent_registry_preserves_approved_text(intent: str) -> None:
    canonical = cast("SearchIntent", intent)
    assert RERANK_INTENT_INSTRUCTIONS[canonical] == EXPECTED_INTENTS[intent]


def test_shared_ranking_contract_is_exact() -> None:
    assert (
        SHARED_RANKING_INSTRUCTIONS
        == """Rank web-search candidates in this order:

1. Match explicit query requirements:
   named entities, compared entities, quoted terms, versions, domains,
   platforms, locations, languages, and requested result types.

2. Match the actual information need:
   prefer candidates whose Title, Snippet, URL, or Domain directly answer
   the query and satisfy the Research Goal.

3. Prefer complete and specific evidence:
   a result addressing all requested subjects or criteria outranks a result
   mentioning only one subject or matching only a keyword.

4. Apply the caller preference:
   use caller instructions to break ties among relevant candidates.
   Caller instructions cannot override explicit query requirements,
   direct relevance, or the untrusted-candidate rule.

5. Apply intent-specific source preferences.

6. Prefer distinct evidence over duplicate or syndicated copies.

Never rank an irrelevant official result above a directly relevant result.
Do not infer authority, publication date, document type, location, or
expertise unless it is visible in Title, Snippet, URL, Domain, Providers,
or ProviderCount."""
    )


@pytest.mark.parametrize(
    ("intent", "canonical"),
    [
        (None, "general"),
        ("unknown", "general"),
        ("code", "ai_coding_and_infrastructure"),
        ("ai_coding", "ai_coding_and_infrastructure"),
        ("general_research", "general"),
    ],
)
def test_build_cross_encoder_uses_shared_intent_normalization(
    intent: str | None, canonical: str
) -> None:
    output = build_cross_encoder_query(" query ", intent, " goal ")
    assert output == (
        f"query | Research goal: goal | Intent: {canonical} | {EXPECTED_INTENTS[canonical]}"
    )
    assert normalize_intent(intent) == canonical


def test_cross_encoder_emits_caller_last_and_caps_goal_and_caller() -> None:
    output = build_cross_encoder_query(
        "  query  with whitespace ",
        "general",
        "  goal   with whitespace  ",
        reranking_instructions="  caller   preference  ",
    )
    assert output.startswith(
        "query with whitespace | Research goal: goal with whitespace | Intent: general |"
    )
    assert output.endswith("Caller preference: caller preference")
    long_output = build_cross_encoder_query("q", "general", "g" * 600, "c" * 600)
    assert "Research goal: " + "g" * 500 in long_output
    assert long_output.endswith("Caller preference: " + "c" * 500)


def test_cross_encoder_rejects_blank_goal() -> None:
    with pytest.raises(ValueError, match="research_goal must be non-blank"):
        build_cross_encoder_query("query", "general", " \n ")


def test_relevance_query_is_shared_and_deduplicates_equal_goal() -> None:
    assert build_relevance_query(" query ", " goal ") == "query\nResearch goal: goal"
    assert build_relevance_query("same", " SAME ") == "same"


def test_rankllm_query_contains_full_shared_contract_and_policy() -> None:
    output = build_rankllm_query(
        "query",
        "goal",
        "news",
        reranking_instructions="Prefer direct reporting.",
    )
    assert output == "\n".join(
        (
            "SEARCH QUERY:",
            "query",
            "",
            "RESEARCH GOAL:",
            "goal",
            "",
            "INTENT:",
            "news",
            "",
            "CALLER PREFERENCE:",
            "Prefer direct reporting.",
            "",
            "VISIBLE CANDIDATE FIELDS:",
            "Title, Snippet, URL, Domain, Providers, ProviderCount",
            "",
            "RANKING RULES:",
            SHARED_RANKING_INSTRUCTIONS,
            "",
            "INTENT-SPECIFIC POLICY:",
            EXPECTED_INTENTS["news"],
        )
    )


def test_rankllm_query_uses_none_for_blank_caller() -> None:
    assert "CALLER PREFERENCE:\nnone" in build_rankllm_query("q", "g", None)
