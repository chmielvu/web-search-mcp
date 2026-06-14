"""Prompt builder for entity extraction from search snippets."""

from __future__ import annotations

from .builders import REASONING_EFFORT_LOW, system_header


def build_entity_extraction_prompt(
    *,
    query: str,
    research_goal: str | None,
    provider_name: str = "worker",
) -> tuple[str, str]:
    goal = research_goal or query
    system = f"""{system_header(REASONING_EFFORT_LOW)}

Extract grounded entities from web search text.
Do not classify intent, rewrite, or judge relevance.
Return JSON only.
Schema:
- entities: array of {{text,label,start,end,confidence}}

Rules:
- Extract only entities explicitly present in the text.
- Labels: package, api, function, class, model, organization, person, website, framework, dataset, topic, tool, other.
- Use exact character offsets from the provided text.
- If no grounded entities are present, return an empty entities array.
- Return compact, valid JSON.
"""
    user = f"""TEXT:
{query}

RESEARCH_GOAL:
{goal}

Return JSON only."""
    return system, user
