"""Prompt helpers for rerank instruction steering."""

from __future__ import annotations


def build_rerank_instruction(
    query: str, query_type: str | None, research_goal: str | None
) -> str:
    parts = [f"Query: {query}"]
    if query_type:
        parts.append(f"Query type: {query_type}")
    if research_goal:
        parts.append(f"Research goal: {research_goal}")
    return "\n".join(parts)
