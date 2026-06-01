from __future__ import annotations

from typing import Any

from .normalize import normalize_query
from .query_rewrite_models import (
    QueryDecompositionOutput,
    RewriteIntent,
    SubQuestion,
)

CLASSIFIER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["code", "general_research", "comparison"],
        },
        "should_decompose": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "routing": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "keyword": {"type": "boolean"},
                "neural": {"type": "boolean"},
                "community": {"type": "boolean"},
            },
            "required": ["keyword", "neural", "community"],
        },
    },
    "required": ["intent", "should_decompose", "confidence", "routing"],
}

DECOMPOSITION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "should_decompose": {"type": "boolean"},
        "sub_questions": {
            "type": "array",
            "minItems": 0,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "question": {"type": "string"},
                    "target": {
                        "type": "string",
                        "enum": ["keyword", "neural", "community", "all"],
                    },
                    "why": {"type": "string"},
                    "weight": {"type": "number", "minimum": 0.8, "maximum": 1.2},
                },
                "required": ["question", "target", "why", "weight"],
            },
        },
    },
    "required": ["should_decompose", "sub_questions"],
}

CLASSIFIER_SYSTEM_PROMPT = """You classify AI-agent web search queries.

Return JSON only matching the provided schema.
Allowed intent values:
- code
- general_research
- comparison

Routing guidance:
- keyword: docs, APIs, release notes, error messages, precise lookups
- neural: conceptual synthesis, grounded answers, natural-language research
- community: bugs, workarounds, opinions, discussions, developer experiences

Set should_decompose=true only when the query clearly contains multiple independent search goals.
Do not explain your reasoning.

Examples:
- Query: "FastMCP prompt timeout docs" -> {"intent":"code","should_decompose":false,"confidence":0.96,"routing":{"keyword":true,"neural":true,"community":false}}
- Query: "React 19 vs Vue 4 SSR performance and developer experience" -> {"intent":"comparison","should_decompose":true,"confidence":0.92,"routing":{"keyword":true,"neural":true,"community":true}}
- Query: "what is query rewriting in search agents" -> {"intent":"general_research","should_decompose":false,"confidence":0.9,"routing":{"keyword":true,"neural":true,"community":false}}
"""

DECOMPOSITION_SYSTEM_PROMPT = """You decompose one search goal into a small set of standalone search queries.

Return JSON only matching the provided schema.

Rules:
- Generate 2 to 3 sub-questions when decomposition is useful.
- Each sub-question must be self-contained and searchable on its own.
- Keep each sub-question concise.
- Use keyword for docs/specs/errors, neural for synthesis, community for opinions/bugs/workarounds.
- Preserve exact terms from MUST_KEEP_TERMS.
- Do not invent entities or facts.
- Do not explain your reasoning.

Examples:
- Query: "React 19 vs Vue 4 SSR performance and developer experience" ->
  sub_questions for React 19, Vue 4, and developer experience discussion
- Query: "FastMCP ResourcesAsTools PromptsAsTools" ->
  sub_questions for official docs and community issues
"""


def build_classifier_messages(
    *,
    query: str,
    research_goal: str | None,
    must_keep_terms: list[str],
) -> list[dict[str, str]]:
    must_keep = "\n".join(f"- {term}" for term in must_keep_terms) or "- none"
    goal = research_goal or query
    return [
        {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"""RAW_QUERY:
{query}

RESEARCH_GOAL:
{goal}

MUST_KEEP_TERMS:
{must_keep}

Return JSON only.""",
        },
    ]


def build_decomposition_messages(
    *,
    query: str,
    research_goal: str | None,
    must_keep_terms: list[str],
    intent: RewriteIntent,
    routing: dict[str, bool] | None = None,
) -> list[dict[str, str]]:
    must_keep = "\n".join(f"- {term}" for term in must_keep_terms) or "- none"
    goal = research_goal or query
    routing_lines = ""
    if routing:
        routing_lines = "\n".join(f"- {k}: {v}" for k, v in routing.items())
    return [
        {"role": "system", "content": DECOMPOSITION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"""RAW_QUERY:
{query}

RESEARCH_GOAL:
{goal}

INTENT:
{intent}

ROUTING:
{routing_lines or "- none"}

MUST_KEEP_TERMS:
{must_keep}

Return JSON only.""",
        },
    ]


def normalize_sub_questions(
    output: QueryDecompositionOutput,
    *,
    max_subquestions: int,
) -> QueryDecompositionOutput:
    seen: set[str] = set()
    cleaned: list[SubQuestion] = []
    for item in output.sub_questions:
        normalized = normalize_query(item.question)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(
            item.model_copy(
                update={"question": normalized, "why": normalize_query(item.why)}
            )
        )
        if len(cleaned) >= max_subquestions:
            break
    return QueryDecompositionOutput(
        should_decompose=output.should_decompose and len(cleaned) >= 1,
        sub_questions=cleaned,
    )
