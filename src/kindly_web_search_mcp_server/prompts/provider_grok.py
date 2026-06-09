"""Prompt builders for Grok search tasks."""

from __future__ import annotations

from .builders import anchor_today


def build_provider_grok_prompt(
    *, query: str, research_goal: str | None, provider_name: str = "grok"
) -> tuple[str, str]:
    goal = research_goal or query
    system = f"""<identity>
Search assistant for {provider_name}. Today: {anchor_today()}.
</identity>
<instructions>
Search the web and cite concise results.
Prefer fresh sources and keep output compact.
</instructions>"""
    user = f"""<query>{query}</query>
<goal>{goal}</goal>"""
    return system, user
