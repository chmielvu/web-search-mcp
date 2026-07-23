"""Prompt builders for query understanding."""

from __future__ import annotations

from ..search.intents import SearchIntent
from .builders import REASONING_EFFORT_LOW, system_header


def build_query_understanding_prompt(
    *,
    query: str,
    research_goal: str | None,
    intent: SearchIntent | None,
    provider_name: str = "worker",
) -> tuple[str, str]:
    goal = research_goal or query
    system = f"""{system_header(REASONING_EFFORT_LOW)}

Classify and annotate web search queries.
Return JSON only.
Schema version 0.3:
- schema_version: "0.3"
- intent: general | ai_coding_and_infrastructure | digital_humanities | comparison | social_media | news
- confidence: 0 to 1
- entities: array of {{text,label,start,end,confidence}}
- preserved_terms: array of exact literals
- compared_entities: array of named items
- time_sensitivity: none | recent | current | historical
- domain_hints: array of short labels
- rationale: short string
- should_decompose: boolean

Rules:
- general = broad exploration or mixed intent.
- ai_coding_and_infrastructure = code, APIs, packages, tooling, build errors.
- digital_humanities = history, archives, corpora, texts.
- comparison = explicit comparison or ranking of named things.
- social_media = platforms like TikTok, Instagram, YouTube, Reddit, trends, influencers.
- news = current events, breaking news, journalism, media coverage.
- If ambiguous or low confidence, choose general.
- Extract only grounded entities. Preserve exact literals and identifiers.
- Return compact, valid JSON.
"""
    user = f"""RAW_QUERY:
{query}

RESEARCH_GOAL:
{goal}

INTENT_HINT:
{intent or "none"}

Return JSON only."""
    return system, user
