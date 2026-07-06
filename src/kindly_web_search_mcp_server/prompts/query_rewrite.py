"""Prompt builders for rewrite workers."""

from __future__ import annotations

from ..search.intents import SearchIntent
from .builders import (
    REASONING_EFFORT_LOW,
    anchor_today,
    join_terms,
    system_header,
)


def build_query_rewrite_prompt(
    *,
    query: str,
    research_goal: str | None,
    intent: SearchIntent,
    must_keep_terms: list[str],
    provider_name: str,
    max_variants: int = 2,
) -> tuple[str, str]:
    goal = research_goal or query
    must_keep = join_terms(must_keep_terms)
    intent_directives = {
        "general": "Create concise variants that improve retrieval without changing meaning.",
        "ai_coding_and_infrastructure": "Prefer docs, issues, release notes, and exact technical terms.",
        "digital_humanities": "Prefer archives, primary sources, editions, corpora, and scholarly context.",
        "comparison": "Preserve compared items. Produce contrastive search variants.",
        "social_media": "Prefer platform names, trending topics, and current engagement metrics.",
        "news": "Prefer recent events, news sources, and time-sensitive queries.",
    }[intent]
    system = f"""{system_header(REASONING_EFFORT_LOW)}

Rewrite search queries. {intent_directives}
Today is {anchor_today()}.
Return JSON only with a top-level `variants` array.
Produce exactly {max_variants} variant{"s" if max_variants != 1 else ""}.
Each variant has: kind, target, query, why, weight.
Target must be keyword, community, or neural.
Preserve every must-keep term exactly.
"""
    user = f"""RAW_QUERY:
{query}

RESEARCH_GOAL:
{goal}

INTENT:
{intent}

MUST_KEEP_TERMS:
{must_keep}

Return JSON only."""
    return system, user
