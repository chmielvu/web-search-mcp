"""LLM judge prompt templates for live pipeline search quality scoring.

Provides a system prompt and user-prompt builder for an LLM-as-judge that
evaluates search results across four dimensions (relevance, accuracy,
completeness, source_quality) plus an overall score and rationale.

This module is used in the *live* search pipeline (not the offline eval
benchmarking in evals/judges.py). Scores are persisted to the
judge_evaluations table via analytics/duckdb_store.py.
"""

from __future__ import annotations

import json
import logging
from typing import Any

LOGGER = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = (
    "You are a search quality evaluator. "
    "Assess how well a set of search results satisfies both the user's query and "
    "their explicit research goal. Evaluate exactly four dimensions as scores from "
    "0.0 to 1.0 with grades excellent|good|fair|poor:\n"
    "- relevance: topical match to the query and research goal\n"
    "- accuracy: factual correctness and trustworthiness\n"
    "- completeness: collective coverage of the research goal\n"
    "- source_quality: authority, recency, and reliability\n\n"
    "Respond with ONLY a single JSON object matching this shape (no markdown):\n"
    '{"relevance":{"grade":"good","score":0.8,"rationale":"..."},'
    '"accuracy":{"grade":"good","score":0.8,"rationale":"..."},'
    '"completeness":{"grade":"fair","score":0.6,"rationale":"..."},'
    '"source_quality":{"grade":"good","score":0.8,"rationale":"..."},'
    '"overall_score":0.75,"overall_rationale":"..."}'
)

_DEFAULT_SCORES: dict[str, Any] = {
    "relevance_score": None,
    "accuracy_score": None,
    "completeness_score": None,
    "source_quality_score": None,
    "overall_score": None,
    "rationale": None,
}


def build_judge_user_prompt(
    query: str,
    research_goal: str,
    intent: str,
    results_text: str,
    tool_name: str,
) -> str:
    """Build the user-facing evaluation prompt for a judge LLM.

    Parameters
    ----------
    query : str
        The original user search query.
    research_goal : str
        The user's explicit research objective.
    intent : str
        Inferred search intent (e.g. 'informational', 'navigational',
        'transactional').
    results_text : str
        The search results rendered as text (titles, snippets, URLs).
    tool_name : str
        The name of the search tool used (e.g. 'web_search', 'news_search').

    Returns
    -------
    str
        The formatted user prompt.
    """
    return (
        f"Tool used: {tool_name}\n"
        f"User query: {query}\n"
        f"Research goal: {research_goal}\n"
        f"Search intent: {intent}\n\n"
        f"--- Search results ---\n{results_text}\n"
        f"--- End results ---\n\n"
        f"Please evaluate the quality of these search results."
    )


def parse_judge_response(response_text: str) -> dict[str, Any]:
    """Extract structured scores from an LLM judge response.

    Handles plain JSON, markdown-wrapped JSON (```json ... ```), and
    malformed input gracefully. Returns sensible defaults (all scores None)
    when parsing fails.

    Parameters
    ----------
    response_text : str
        The raw text response from the judge LLM.

    Returns
    -------
    dict[str, Any]
        A dict with keys: relevance_score, accuracy_score, completeness_score,
        source_quality_score, overall_score, rationale. Values are floats or
        None if parsing failed.
    """
    s = (response_text or "").strip()
    if not s:
        LOGGER.debug("judge response empty")
        return dict(_DEFAULT_SCORES)

    # Strip markdown code fences if present
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            inner = parts[1].strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].strip()
            s = inner

    # Last resort: find first balanced { ... }
    if not s.startswith("{"):
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1]

    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        LOGGER.debug("failed to parse judge response: %s", exc)
        return dict(_DEFAULT_SCORES)

    result: dict[str, Any] = {}
    for key in _DEFAULT_SCORES:
        val = data.get(key)
        if val is not None and isinstance(val, (int, float)):
            result[key] = float(val)
        else:
            result[key] = val  # keep as-is (None or unexpected type)

    return result


__all__ = [
    "JUDGE_SYSTEM_PROMPT",
    "build_judge_user_prompt",
    "parse_judge_response",
]
