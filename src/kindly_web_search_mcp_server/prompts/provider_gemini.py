"""Prompt builders for Gemini synthesis/search tasks."""

from __future__ import annotations

from .builders import anchor_today


def build_provider_gemini_prompt(
    *, query: str, research_goal: str | None, provider_name: str = "gemini"
) -> tuple[str, str]:
    goal = research_goal or query
    system = f"""You are a search synthesis assistant for {provider_name}.
Today is {anchor_today()}.
Answer with short grounded bullet points and inline citations.
Prefer current, official, and primary sources.
"""
    user = f"""QUERY:
{query}

RESEARCH_GOAL:
{goal}

Return a concise grounded answer with citations."""
    return system, user
