from __future__ import annotations

from typing import Literal

from fastmcp.prompts import Message


def web_search_workflow_prompt(
    query: str,
    num_results: int = 5,
    depth: Literal["quick", "medium", "deep"] = "medium",
    focus: Literal["code", "academic", "news", "general"] = "general",
) -> list[Message]:
    """Guided research workflow.

    Args:
        query: the research question.
        num_results: target number of results (3=fast, 5=standard, 7=broad, max 10).
        depth: quick / medium / deep routing.
        focus: code / academic / news / general bias.
    """
    lines = [
        f"Research question: {query!r}",
        f"Target result count: {num_results} | Depth: {depth} | Focus: {focus}",
        "",
        "Follow this routing:",
    ]
    if depth == "quick":
        lines.append(
            "- QUICK: use quick_web_search for ranked excerpts/citations or gemini_search for a synthesized answer."
        )
    elif depth == "medium":
        lines.append(
            "- MEDIUM: web_search -> triage by provider_count>=2 -> batch_get_content on top 2-3 URLs."
        )
    else:
        lines.append(
            "- DEEP: web_search(num_results=7) -> batch_get_content(5) -> cross-check with academic_search."
        )
    if focus == "code":
        lines.append(
            "- CODE: bias toward github.com / stackoverflow.com; use rewrite=false for error hashes."
        )
    elif focus == "academic":
        lines.append(
            "- ACADEMIC: academic_search first (field/venue/year filters); cross-check 2+ papers."
        )
    elif focus == "news":
        lines.append("- NEWS: prefer grok_search for real-time/social signals.")
    else:
        lines.append("- GENERAL: balance docs/articles/community; docs first.")
    lines += [
        "",
        "Execution:",
        "1. Evaluate results: provider_count>=2 is a strong signal; verify domain if 1 or missing.",
        "2. Read pages: get_content (single) or batch_get_content (multiple); on TimeoutError see docs://workflow.",
        "3. Gap analysis: terminate when 3 independent sources agree, or 2 consecutive rounds add nothing.",
    ]
    return [Message("\n".join(lines), role="user")]


def query_refinement_prompt(
    original_query: str,
    failed_attempts: list[str] | None = None,
    reason: str | None = None,
) -> list[Message]:
    """Plan query variants after a failed or sparse search.

    Args:
        original_query: the query that returned poor results.
        failed_attempts: queries already tried.
        reason: why the search failed (sparse, off-topic, blocked, ...).
    """
    tried = failed_attempts or []
    lines = [
        f"Original query: {original_query!r}",
        f"Failed attempts: {', '.join(tried) if tried else 'None'}",
        f"Failure reason: {reason or 'Sparse or off-topic results'}",
        "",
        "Generate 3 query variants:",
        "1. Broaden: replace jargon with general terms.",
        "2. Pinpoint: exact literals (rewrite=false) or double quotes.",
        "3. Decompose: split into independent sub-queries.",
    ]
    return [Message("\n".join(lines), role="user")]
