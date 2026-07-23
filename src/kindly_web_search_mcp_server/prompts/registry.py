"""Prompt registry for worker families."""

from __future__ import annotations

from ..search.intents import SearchIntent
from .provider_gemini import build_provider_gemini_prompt
from .provider_grok import build_provider_grok_prompt

from .entity_extraction import build_entity_extraction_prompt
from .query_understanding import build_query_understanding_prompt


def build_prompt(
    name: str,
    *,
    query: str,
    research_goal: str | None = None,
    intent: SearchIntent | None = None,
    provider_name: str = "worker",
) -> tuple[str, str]:
    if name == "query_understanding":
        return build_query_understanding_prompt(
            query=query,
            research_goal=research_goal,
            intent=intent,
            provider_name=provider_name,
        )
    if name == "entity_extraction":
        return build_entity_extraction_prompt(
            query=query,
            research_goal=research_goal,
            provider_name=provider_name,
        )
    if name == "gemini_search":
        return build_provider_gemini_prompt(
            query=query,
            research_goal=research_goal,
            provider_name=provider_name,
        )
    if name == "grok_search":
        return build_provider_grok_prompt(
            query=query,
            research_goal=research_goal,
            provider_name=provider_name,
        )
    if name == "rerank":
        return (
            "Use the query and research goal to prioritize sources.",
            f"QUERY:\n{query}\n\nRESEARCH_GOAL:\n{research_goal or query}",
        )
    raise KeyError(name)
