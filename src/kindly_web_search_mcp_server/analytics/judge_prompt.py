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
    "Your task is to assess how well a set of search results satisfies a user's query, "
    "considering the user's intent. Evaluate on exactly four dimensions, each scored "
    "as a float between 0.0 and 1.0:\n"
    "- relevance: how well each result matches the query topic\n"
    "- accuracy: factual correctness and trustworthiness of the information\n"
    "- completeness: whether the results collectively cover the user's information need\n"
    "- source_quality: authority, recency, and reliability of the sources\n\n"
    "Also produce:\n"
    "- overall_score: a single holistic quality score (0.0-1.0) for the result set\n"
    "- rationale: a brief 1-3 sentence explanation of the scores\n\n"
    "Respond with ONLY a single JSON object in exactly this format "
    "(no markdown fences, no extra text):\n"
    '{"relevance_score": 0.85, "accuracy_score": 0.7, "completeness_score": 0.6, '
    '"source_quality_score": 0.8, "overall_score": 0.75, "rationale": "Brief explanation"}'
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
    intent: str,
    results_text: str,
    tool_name: str,
) -> str:
    """Build the user-facing evaluation prompt for a judge LLM.

    Parameters
    ----------
    query : str
        The original user search query.
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