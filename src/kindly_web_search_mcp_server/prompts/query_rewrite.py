"""Prompt builders for rewrite workers."""

from __future__ import annotations

from ..search.intents import SearchIntent
from .builders import anchor_today, join_terms, provider_style


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
    system = {
        "general": """You rewrite search queries for broad research.
Create concise variants that improve retrieval without changing meaning.""",
        "ai_coding": """You rewrite technical search queries for code and API lookup.
Prefer docs, issues, release notes, and exact technical terms.""",
        "digital_humanities": """You rewrite humanities research queries.
Prefer archives, primary sources, editions, corpora, and scholarly context.""",
        "comparison": """You rewrite comparison queries.
Preserve the compared items and produce contrastive search variants.""",
    }[intent]
    system += f"""

Today is {anchor_today()}.
Return JSON only with a top-level `variants` array.
Each variant has: kind, target, query, why, weight.
Target must be keyword, community, or neural.
Preserve every must-keep term exactly.
This prompt is tuned for {provider_style(provider_name)} and GPT-OSS-style workers.
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
