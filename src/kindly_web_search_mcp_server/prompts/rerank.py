"""Canonical prompt builders for the shared web-search reranking funnel."""

from __future__ import annotations

from ..search.intents import SearchIntent, normalize_intent


RERANK_INTENT_INSTRUCTIONS: dict[SearchIntent, str] = {
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


SHARED_RANKING_INSTRUCTIONS = """Rank web-search candidates in this order:

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


def _normalize_prompt_text(value: str | None, *, cap: int | None = None) -> str:
    normalized = " ".join((value or "").split()).strip()
    return normalized[:cap] if cap is not None else normalized


def _prompt_inputs(
    user_query: str,
    research_goal: str,
    reranking_instructions: str | None,
) -> tuple[str, str, str]:
    if not research_goal or not research_goal.strip():
        raise ValueError("research_goal must be non-blank")
    return (
        _normalize_prompt_text(user_query),
        _normalize_prompt_text(research_goal, cap=500),
        _normalize_prompt_text(reranking_instructions, cap=500),
    )


def build_relevance_query(user_query: str, research_goal: str) -> str:
    """Build the shared semantic-recall query used by BM25 and embeddings."""
    normalized_query, normalized_goal, _ = _prompt_inputs(user_query, research_goal, None)
    if normalized_query.casefold() == normalized_goal.casefold():
        return normalized_query
    return f"{normalized_query}\nResearch goal: {normalized_goal}"


def build_cross_encoder_query(
    user_query: str,
    query_type: str | None,
    research_goal: str,
    reranking_instructions: str | None = None,
) -> str:
    """Build the compact pointwise cross-encoder request."""
    normalized_query, normalized_goal, caller = _prompt_inputs(
        user_query, research_goal, reranking_instructions
    )
    intent = normalize_intent(query_type)
    segments = [
        normalized_query,
        f"Research goal: {normalized_goal}",
        f"Intent: {intent}",
        RERANK_INTENT_INSTRUCTIONS[intent],
    ]
    if caller:
        segments.append(f"Caller preference: {caller}")
    return " | ".join(segments)


def build_rankllm_query(
    user_query: str,
    research_goal: str,
    query_type: str | None,
    reranking_instructions: str | None = None,
) -> str:
    """Build the full labeled listwise RankLLM request."""
    normalized_query, normalized_goal, caller = _prompt_inputs(
        user_query, research_goal, reranking_instructions
    )
    intent = normalize_intent(query_type)
    return "\n".join(
        (
            "SEARCH QUERY:",
            normalized_query,
            "",
            "RESEARCH GOAL:",
            normalized_goal,
            "",
            "INTENT:",
            intent,
            "",
            "CALLER PREFERENCE:",
            caller or "none",
            "",
            "VISIBLE CANDIDATE FIELDS:",
            "Title, Snippet, URL, Domain, Providers, ProviderCount",
            "",
            "RANKING RULES:",
            SHARED_RANKING_INSTRUCTIONS,
            "",
            "INTENT-SPECIFIC POLICY:",
            RERANK_INTENT_INSTRUCTIONS[intent],
        )
    )
