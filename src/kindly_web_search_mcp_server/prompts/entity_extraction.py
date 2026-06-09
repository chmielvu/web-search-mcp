"""Prompt builder for entity extraction from search snippets."""

from __future__ import annotations

from .builders import provider_style


def build_entity_extraction_prompt(
    *,
    query: str,
    research_goal: str | None,
    provider_name: str = "worker",
) -> tuple[str, str]:
    goal = research_goal or query
    system = f"""You extract only grounded entities from short web search text for {provider_style(provider_name)}.
Today is not needed; do not classify intent, rewrite, or judge relevance.
Return JSON only.
The schema is:
- entities: array of {{text,label,start,end,confidence}}

Rules:
- Extract only entities explicitly present in the text.
- Prefer precise labels such as package, api, function, class, model, organization, person, website, framework, dataset, topic, tool, or other.
- Use exact character offsets from the provided text.
- If no grounded entities are present, return an empty entities array.
- Keep the output compact and valid JSON.
"""
    user = f"""TEXT:
{query}

RESEARCH_GOAL:
{goal}

Return JSON only."""
    return system, user
