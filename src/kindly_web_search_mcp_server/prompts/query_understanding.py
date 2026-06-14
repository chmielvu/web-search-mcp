"""Prompt builders for query understanding."""

from __future__ import annotations

from ..search.intents import SearchIntent
from .builders import REASONING_EFFORT_LOW, join_terms, system_header


def build_query_understanding_prompt(
    *,
    query: str,
    research_goal: str | None,
    intent: SearchIntent | None,
    must_keep_terms: list[str],
    provider_name: str = "worker",
) -> tuple[str, str]:
    goal = research_goal or query
    system = f"""{system_header(REASONING_EFFORT_LOW)}

Classify and annotate web search queries.
Return JSON only.
Schema:
- schema_version: "0.2"
- intent: general | ai_coding | digital_humanities | comparison
- confidence: 0 to 1
- entities: array of {{text,label,start,end,confidence}}
- preserved_terms: array of exact literals
- compared_entities: array of named items
- time_sensitivity: none | recent | current | historical
- domain_hints: array of short labels
- provider_hints: object with keyword/neural/community booleans
- rewrite_hints: object with style, variant_count, preserve_order
- rationale: short string

Rules:
- general = broad exploration or mixed intent.
- ai_coding = code, APIs, packages, tooling, build errors.
- digital_humanities = history, archives, corpora, texts.
- comparison = explicit comparison or ranking of named things.
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

MUST_KEEP_TERMS:
{join_terms(must_keep_terms)}

Return JSON only."""
    return system, user
