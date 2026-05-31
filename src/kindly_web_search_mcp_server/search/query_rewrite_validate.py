from __future__ import annotations

import json
from collections.abc import Iterable

from pydantic import ValidationError

from .normalize import normalize_query
from .query_rewrite_models import QueryRewriteOutput, QueryVariant, RewriteIntent

ALLOWED_KINDS_BY_INTENT = {
    "code": {"original", "official_docs", "community_issues"},
    "general_research": {"original", "expanded", "focused"},
    "comparison": {"original", "entity_a", "entity_b"},
}
COMMUNITY_TOKENS = ("github", "issue", "discussion", "stack overflow", "forum", "bug")


def parse_query_rewrite_output(content: str) -> QueryRewriteOutput:
    data = json.loads(content)
    raw_variants = data.get("variants", []) if isinstance(data, dict) else []
    variants: list[QueryVariant] = []
    for item in raw_variants:
        try:
            variants.append(QueryVariant.model_validate(item))
        except ValidationError:
            continue
    return QueryRewriteOutput(variants=variants)


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = normalize_query(item).casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def inject_missing_terms(query: str, must_keep_terms: list[str]) -> str:
    missing = []
    lowered = normalize_query(query).casefold()
    for term in must_keep_terms:
        normalized_term = normalize_query(term)
        if normalized_term and normalized_term.casefold() not in lowered:
            missing.append(normalized_term)
    if not missing:
        return normalize_query(query)
    return normalize_query(f"{query} {' '.join(missing)}")


def validate_keyword_variants(
    variants: Iterable[QueryVariant],
    *,
    intent: RewriteIntent,
    must_keep_terms: list[str],
) -> list[QueryVariant]:
    allowed = ALLOWED_KINDS_BY_INTENT[intent]
    valid: list[QueryVariant] = []
    seen: set[str] = set()
    for variant in variants:
        if variant.target != "keyword" or variant.kind not in allowed:
            continue
        if not _keeps_required_terms(variant.query, must_keep_terms):
            continue
        if _looks_like_prose_answer(variant.query):
            continue
        if variant.kind == "community_issues" and not _has_community_signal(variant.query):
            continue
        key = normalize_query(variant.query).casefold()
        if key in seen:
            continue
        seen.add(key)
        valid.append(variant)
    return valid


def validate_neural_variants(
    variants: Iterable[QueryVariant],
    *,
    must_keep_terms: list[str],
) -> list[QueryVariant]:
    valid: list[QueryVariant] = []
    for variant in variants:
        if variant.target != "neural" or variant.kind != "neural_task":
            continue
        if not _keeps_required_terms(variant.query, must_keep_terms):
            continue
        if _looks_like_keyword_pile(variant.query):
            continue
        valid.append(variant)
    return valid[:1]


COMMUNITY_ALLOWED_KINDS = frozenset({"original", "practitioner_opinion", "bug_report", "how_to"})


def validate_community_variants(
    variants: Iterable[QueryVariant],
    *,
    must_keep_terms: list[str],
) -> list[QueryVariant]:
    valid: list[QueryVariant] = []
    seen: set[str] = set()
    for variant in variants:
        if variant.target != "community" or variant.kind not in COMMUNITY_ALLOWED_KINDS:
            continue
        if not _keeps_required_terms(variant.query, must_keep_terms):
            continue
        if _looks_like_keyword_pile(variant.query):
            continue
        key = normalize_query(variant.query).casefold()
        if key in seen:
            continue
        seen.add(key)
        valid.append(variant)
    return valid


def _keeps_required_terms(query: str, must_keep_terms: list[str]) -> bool:
    normalized = normalize_query(query).casefold()
    return all(normalize_query(term).casefold() in normalized for term in must_keep_terms)


def _looks_like_prose_answer(query: str) -> bool:
    lowered = query.strip().casefold()
    return lowered.startswith(("here is", "this query", "the answer", "i need"))


def _has_community_signal(query: str) -> bool:
    lowered = query.casefold()
    return any(token in lowered for token in COMMUNITY_TOKENS)


def _looks_like_keyword_pile(query: str) -> bool:
    words = query.split()
    if len(words) < 8:
        return False
    punctuation = query.count(",") + query.count(".")
    if punctuation > 0:
        return False
    lowered = query.casefold()
    return not any(token in lowered for token in ("find ", "compare ", "identify ", "verify "))
