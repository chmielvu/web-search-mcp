"""LLM fan-out prompt, schema, parsing, and normalization."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from .normalize import normalize_query
from .query_rewrite_models import QueryFanoutOutput, QueryVariant, RewriteIntent

BRANCH_KINDS = (
    "related",
    "implicit",
    "comparative",
    "reformulation",
    "entity_expanded",
)

BRANCH_TARGETS = ("keyword", "neural", "community", "all")

FANOUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rationale": {"type": "string"},
        "branches": {
            "type": "array",
            "minItems": 8,
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": list(BRANCH_KINDS)},
                    "branch_type": {"type": "string", "enum": list(BRANCH_KINDS)},
                    "target": {"type": "string", "enum": list(BRANCH_TARGETS)},
                    "query": {"type": "string"},
                    "why": {"type": "string"},
                    "reason": {"type": "string"},
                    "weight": {"type": "number", "minimum": 0.8, "maximum": 1.2},
                    "must_keep_terms": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": [
                    "kind",
                    "branch_type",
                    "target",
                    "query",
                    "why",
                    "reason",
                    "weight",
                    "must_keep_terms",
                    "max_results",
                ],
            },
        },
    },
    "required": ["rationale", "branches"],
}

FANOUT_SYSTEM_PROMPT = """You generate a fan-out plan for agentic web search.

Return JSON only matching the provided schema.

Hard rules:
- Generate 8 to 10 branches.
- Each branch must be independently searchable.
- Branch categories must be one of: related, implicit, comparative, reformulation, entity_expanded.
- Each branch must include branch_type, target, query, why, reason, weight, must_keep_terms, and max_results.
- Preserve every MUST_KEEP_TERMS item exactly in every branch.
- Use the research goal as the main objective.
- Use the raw query only to recover literal terms, entities, and constraints.
- Keep branch queries short, precise, and different from each other.
- Use keyword for docs, specs, release notes, and exact lookup work.
- Use neural for synthesis, grounded understanding, and natural-language framing.
- Use community for bugs, workarounds, and practitioner reports.
- Use all only when the branch should be searched everywhere.
- Do not invent facts, entities, versions, or citations.
- reason must be compact and explain why the branch exists.
- why must not be empty.
- Do not produce duplicates or near-duplicates.
"""


def build_fanout_messages(
    *,
    query: str,
    research_goal: str | None,
    must_keep_terms: list[str],
    intent: RewriteIntent,
    active_provider_names: list[str],
    routing: dict[str, bool] | None = None,
) -> list[dict[str, str]]:
    must_keep = "\n".join(f"- {term}" for term in must_keep_terms) or "- none"
    goal = research_goal or query
    routing_lines = ""
    if routing:
        routing_lines = "\n".join(f"- {name}: {enabled}" for name, enabled in routing.items())
    providers = "\n".join(f"- {provider}" for provider in active_provider_names) or "- none"
    return [
        {"role": "system", "content": FANOUT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"""CURRENT_DATE:
{date.today().isoformat()}

RAW_QUERY:
{query}

RESEARCH_GOAL:
{goal}

INTENT:
{intent}

ACTIVE_PROVIDERS:
{providers}

ROUTING:
{routing_lines or "- none"}

MUST_KEEP_TERMS:
{must_keep}

Return JSON only.""",
        },
    ]


def parse_fanout_output(content: str) -> QueryFanoutOutput:
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("Fan-out response must be a JSON object")
    branches = data.get("branches")
    if not isinstance(branches, list):
        raise ValueError("Fan-out response missing branches array")
    if not 8 <= len(branches) <= 10:
        raise ValueError("Fan-out response must contain 8 to 10 branches")
    for index, branch in enumerate(branches):
        if not isinstance(branch, dict):
            raise ValueError(f"Fan-out branch {index} must be an object")
        required_fields = (
            "kind",
            "branch_type",
            "target",
            "query",
            "why",
            "reason",
            "weight",
            "must_keep_terms",
            "max_results",
        )
        missing = [field for field in required_fields if field not in branch]
        if missing:
            raise ValueError(
                f"Fan-out branch {index} missing required fields: {', '.join(missing)}"
            )
        for field in ("kind", "branch_type", "target", "query", "why", "reason"):
            if not isinstance(branch[field], str) or not branch[field].strip():
                raise ValueError(
                    f"Fan-out branch {index} field '{field}' must be a non-empty string"
                )
    return QueryFanoutOutput.model_validate(data)


def normalize_fanout_output(
    output: QueryFanoutOutput,
    *,
    must_keep_terms: list[str],
    max_branches: int,
) -> QueryFanoutOutput:
    rationale = normalize_query(output.rationale)
    seen: set[str] = set()
    branches: list[QueryVariant] = []
    for branch in output.branches:
        normalized_query = normalize_query(branch.query)
        if not normalized_query:
            continue
        key = normalized_query.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged_keep_terms = _merge_terms(must_keep_terms, branch.must_keep_terms)
        branch_kind = normalize_query(branch.branch_type or branch.kind)
        reason = normalize_query(branch.reason or branch.why)
        branches.append(
            branch.model_copy(
                update={
                    "query": normalized_query,
                    "why": normalize_query(branch.why),
                    "branch_type": branch_kind,
                    "reason": reason,
                    "must_keep_terms": merged_keep_terms,
                }
            )
        )
        if len(branches) >= max_branches:
            break
    return QueryFanoutOutput(rationale=rationale, branches=branches)


def _merge_terms(left: list[str], right: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for term in [*left, *right]:
        normalized = normalize_query(term)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged
