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
) -> tuple[str, str]:
    goal = research_goal or query
    must_keep = join_terms(must_keep_terms)
    intent_directives = {
        "general": "Create concise variants that improve retrieval without changing meaning.",
        "ai_coding": "Prefer docs, issues, release notes, and exact technical terms.",
        "digital_humanities": "Prefer archives, primary sources, editions, corpora, and scholarly context.",
        "comparison": "Preserve compared items. Produce contrastive search variants.",
    }[intent]
    system = f"""{system_header(REASONING_EFFORT_LOW)}

Rewrite search queries. {intent_directives}
Today is {anchor_today()}.
Return JSON only with a top-level `variants` array.
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
