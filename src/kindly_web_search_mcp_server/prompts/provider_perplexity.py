"""Prompt builders for Perplexity-style search tasks."""

from __future__ import annotations

from .builders import anchor_today


def build_provider_perplexity_prompt(
    *, query: str, research_goal: str | None, provider_name: str = "perplexity"
) -> tuple[str, str]:
    goal = research_goal or query
    system = f"""You are a concise web research assistant for {provider_name}.
Today is {anchor_today()}.
Return factual answers with numbered citations and no filler."""
    user = f"""QUERY:
{query}

RESEARCH_GOAL:
{goal}

Answer with numbered citations."""
    return system, user
