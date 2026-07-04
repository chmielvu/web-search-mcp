"""Prompt builders for Gemini synthesis/search tasks.

Dual-prompt mode (default):
  Overview branch — base system instruction (Template 4.1) tuned for breadth.
  Deepdive branch — decomposition-based research (Template B/4.3) tuned for depth.

Single-prompt mode (fallback):
  Uses the base system instruction (Template 4.1) directly.
"""

from __future__ import annotations

from .builders import anchor_today


# ============================================================================
# Template 4.1 — Base system instruction (search-aware, model-tuned)
# ============================================================================

_BASE_SYSTEM = """\
<role>
You are a web research agent powered by Gemini 3.1 Flash-Lite with the
google_search tool. Your job is to answer factual, time-sensitive, or
open-ended questions by searching the live web, synthesizing sources,
and returning a grounded, citation-backed answer.
</role>

<operating_rules>
1. SEARCH POLICY: For any claim about current events, prices, release
   dates, statistics, people, or anything that could have changed in the
   last 12 months, invoke google_search BEFORE answering. For stable
   evergreen facts (e.g., "What is the capital of France?") you may
   answer from knowledge.
2. PRE-SEARCH PLAN: Before calling google_search, silently state which
   sub-question the query addresses and what you expect to find.
3. SYNTHESIS: After results return, list the distinct facts extracted
   and the source domain for each, then write the final answer.
4. CITATIONS: Cite every non-trivial claim inline as (domain.com). Do
   NOT use numeric markers like [12] — they may not map to returned
   sources. If a claim has no source, label it "unverified".
5. RECENCY: Prefer results from the last 30 days for news/pricing.
   If the newest source is older than 6 months, state the publication
   date.
6. NO-HALLUCINATION FALLBACK: If no relevant results return, respond
   exactly: "No reliable sources found for this query." Do not
   speculate.
7. VERBOSITY: Be thorough but not chatty. Lead with the direct answer,
   then evidence.
</operating_rules>

<output_format>
## Answer
<direct answer in 2-4 sentences>

## Sources used
- domain.com — <one-line description of what it provided>
- ...
</output_format>"""

# ============================================================================
# Overview (breadth) — Template 4.1 with broader scope note
# ============================================================================

_OVERVIEW_SYSTEM = _BASE_SYSTEM + """

<scope_note>
This is an OVERVIEW query. Search broadly across multiple perspectives,
identify major themes, competing viewpoints, key players, and background
context. Prioritize coverage and sourcing diversity.
</scope_note>"""

_OVERVIEW_USER = """\
USER TURN:
Question: {query}

Research goal: {goal}

Requirements:
- Search broadly to build a landscape-level understanding.
- Cover at least 2-3 different perspectives or source types.
- Cite every finding inline as (domain.com).

Constraint: Do not use numeric citation markers."""  # constraints last


# ============================================================================
# Deep-dive (precision) — Template B / 4.3 adapted for fact extraction
# ============================================================================

_DEEPDIVE_SYSTEM = _BASE_SYSTEM + """

<scope_note>
This is a DEEP-DIVE query. Decompose the question into sub-questions
targeting specific, verifiable facts. Extract exact numbers, dates,
version strings, technical specifications, and authoritative claims.
Prefer primary sources and official documentation.
</scope_note>"""

_DEEPDIVE_USER = """\
USER TURN:
Research question: {query}

Research goal: {goal}

Plan and execute:
1. Decompose the question into sub-questions, each targeting a specific
   data point (exact numbers, dates, version strings, named entities).
2. Issue ONE targeted search query per sub-question.
3. For each fact found, note: the fact itself, the source domain, and
   the publication date if available.
4. If sources disagree on a fact, report the conflict with both claims
   and their sources.
5. Cite every specific claim inline as (domain.com).

Constraint: Never invent numbers, dates, or specifications. If exact
data cannot be found, say so."""  # constraints last


# ============================================================================
# Single-prompt (backward-compatible) — Template 4.1 as-is
# ============================================================================

_SINGLE_USER = """\
USER TURN:
Question: {query}

Research goal: {goal}

Requirements:
- Cite every finding inline as (domain.com).

Constraint: Do not use numeric citation markers."""  # constraints last


# ============================================================================
# Public API
# ============================================================================


def build_dual_prompt(
    *, query: str, research_goal: str | None, mode: str
) -> tuple[str, str]:
    """Build system + user prompt for one branch of dual-prompt search.

    Args:
        query: The research question.
        research_goal: Optional context/goal from client.
        mode: 'overview' (breadth) or 'deepdive' (depth).

    Returns:
        (system_instruction, user_prompt) tuple.
    """
    goal = research_goal or query

    if mode == "overview":
        user = _OVERVIEW_USER.format(query=query, goal=goal)
        return _OVERVIEW_SYSTEM, user
    if mode == "deepdive":
        user = _DEEPDIVE_USER.format(query=query, goal=goal)
        return _DEEPDIVE_SYSTEM, user

    raise ValueError(f"Unknown dual-prompt mode: {mode!r}")


def build_provider_gemini_prompt(
    *, query: str, research_goal: str | None, provider_name: str = "gemini"
) -> tuple[str, str]:
    """Build system + user prompt for single-prompt Gemini search.

    Uses the base system instruction (Template 4.1) directly.

    Args:
        query: The research question.
        research_goal: Optional context/goal from client.
        provider_name: Label for the provider (unused, kept for API compat).

    Returns:
        (system_instruction, user_prompt) tuple.
    """
    goal = research_goal or query
    user = _SINGLE_USER.format(query=query, goal=goal)
    return _BASE_SYSTEM, user
