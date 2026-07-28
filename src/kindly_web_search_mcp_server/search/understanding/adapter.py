"""Pure-Python normalization for the hosted GLiNER2 contract."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from ...entity.default_schema import (
    DEFAULT_CONTENT_LABELS,
    DEFAULT_QUERY_LABELS,
    DEFAULT_QUERY_RELATIONS,
)
from ...entity.models import EntityRelation, EntitySpan
from ...entity.postprocess import postprocess_entities
from ...heuristics.query_features import _langs_from_text
from ..intents import SearchIntent, normalize_intent
from .models import QueryUnderstandingResult


class QueryUnderstandingContractError(ValueError):
    """Raised when the hosted service response is not the normalized contract."""


@dataclass(frozen=True, slots=True)
class NormalizedQueryResponse:
    understanding: QueryUnderstandingResult
    model_version: str
    latency_ms: float
    warnings: tuple[str, ...] = ()


_TECHNICAL_LABELS = frozenset(
    {
        "package",
        "version",
        "api_function",
        "error_class",
        "repo_ref",
        "cli_flag",
        "model_id",
        "file_path",
        "env_var",
        "language",
        "platform",
        "provider",
        "tool",
        "topic",
    }
)
_COMPARISON_FALLBACK_LABELS = frozenset(
    {"package", "product", "model_id", "provider", "platform", "repo_ref"}
)
_CURRENT_TERMS = re.compile(r"\b(?:current|currently|now|today|latest)\b", re.IGNORECASE)
_RECENT_TERMS = re.compile(r"\b(?:recent|recently|this\s+week|this\s+month)\b", re.IGNORECASE)
_HISTORICAL_TERMS = re.compile(
    r"\b(?:historical|history|formerly|deprecated|past)\b", re.IGNORECASE
)
_COMPARISON_TERMS = re.compile(r"\b(?:compare|versus|vs\.?|compared)\b", re.IGNORECASE)


def _unwrap_response(data: Any) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise QueryUnderstandingContractError("query understanding response must be an object")
    if isinstance(data.get("result"), Mapping):
        data = data["result"]
    required = {"intent", "confidence", "entities", "relations", "model_version", "latency_ms"}
    missing = sorted(required.difference(data))
    if missing:
        raise QueryUnderstandingContractError(f"missing response fields: {', '.join(missing)}")
    if not isinstance(data["model_version"], str) or not data["model_version"].strip():
        raise QueryUnderstandingContractError("model_version must be a non-empty string")
    if isinstance(data["latency_ms"], bool) or not isinstance(data["latency_ms"], (int, float)):
        raise QueryUnderstandingContractError("latency_ms must be numeric")
    if float(data["latency_ms"]) < 0:
        raise QueryUnderstandingContractError("latency_ms must be non-negative")
    confidence = data["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise QueryUnderstandingContractError("confidence must be numeric")
    if not 0.0 <= float(confidence) <= 1.0:
        raise QueryUnderstandingContractError("confidence must be between 0 and 1")
    if not isinstance(data["entities"], (Mapping, list)):
        raise QueryUnderstandingContractError("entities must be grouped or a list")
    if not isinstance(data["relations"], (Mapping, list)):
        raise QueryUnderstandingContractError("relations must be grouped or a list")
    return data


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None


def _is_offset(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _find_unused_occurrence(
    source: str, text: str, used: set[tuple[int, int]]
) -> tuple[int, int] | None:
    cursor = 0
    while True:
        start = source.find(text, cursor)
        if start < 0:
            return None
        end = start + len(text)
        if (start, end) not in used:
            return start, end
        cursor = start + 1


def _ground_span(
    raw: Any,
    source: str,
    *,
    default_label: str | None,
    known_labels: Mapping[str, str],
    used_missing_offsets: set[tuple[int, int]],
    warnings: list[str],
) -> EntitySpan | None:
    if not isinstance(raw, Mapping):
        warnings.append("entity-not-object")
        return None
    text = raw.get("text", raw.get("entity"))
    label = raw.get("label", raw.get("entity_type", default_label))
    if not isinstance(text, str) or not text or not text.strip():
        warnings.append("entity-empty-text")
        return None
    if not isinstance(label, str) or label not in known_labels:
        warnings.append("entity-unknown-label")
        return None

    start, end = raw.get("start"), raw.get("end")
    if _is_offset(start) and _is_offset(end):
        start_i, end_i = cast(int, start), cast(int, end)
        if not (0 <= start_i < end_i <= len(source)) or source[start_i:end_i] != text:
            warnings.append("entity-offset-mismatch")
            return None
        start, end = start_i, end_i
    else:
        occurrence = _find_unused_occurrence(source, text, used_missing_offsets)
        if occurrence is None:
            warnings.append("entity-offset-unrecoverable")
            return None
        start, end = occurrence
        used_missing_offsets.add(occurrence)

    confidence = _number(raw.get("confidence", raw.get("score")))
    return EntitySpan(text=text, label=label, start=start, end=end, confidence=confidence)


def _entity_rows(grouped: Mapping[str, Any] | list[Any]) -> list[tuple[str | None, Any]]:
    if isinstance(grouped, list):
        return [(None, item) for item in grouped]
    rows: list[tuple[str | None, Any]] = []
    for label, values in grouped.items():
        if isinstance(values, list):
            rows.extend((str(label), value) for value in values)
    return rows


def normalize_content_entities(response: Any, source: str) -> list[EntitySpan]:
    """Normalize an entity-only service response and ground offsets in ``source``."""
    if isinstance(response, Mapping) and isinstance(response.get("result"), Mapping):
        response = response["result"]
    if isinstance(response, Mapping) and isinstance(response.get("results"), Mapping):
        response = response["results"]
    if isinstance(response, Mapping):
        response = response.get("entities", response)
    if not isinstance(response, (Mapping, list)):
        raise QueryUnderstandingContractError("entity response must contain grouped entities")
    warnings: list[str] = []
    used_missing_offsets: set[tuple[int, int]] = set()
    normalized: list[EntitySpan] = []
    for default_label, raw in _entity_rows(response):
        span = _ground_span(
            raw,
            source,
            default_label=default_label,
            known_labels=DEFAULT_CONTENT_LABELS,
            used_missing_offsets=used_missing_offsets,
            warnings=warnings,
        )
        if span is not None:
            normalized.append(span)
    return postprocess_entities(normalized)


def _match_relation_endpoint(
    raw: Any, entities: list[EntitySpan], source: str, warnings: list[str]
) -> EntitySpan | None:
    if not isinstance(raw, Mapping):
        warnings.append("relation-endpoint-not-object")
        return None
    raw_text = raw.get("text")
    raw_start, raw_end = raw.get("start"), raw.get("end")
    candidates = entities
    if _is_offset(raw_start) and _is_offset(raw_end):
        candidates = [
            entity for entity in entities if entity.start == raw_start and entity.end == raw_end
        ]
    if isinstance(raw_text, str) and raw_text:
        candidates = [entity for entity in candidates if entity.text == raw_text] or candidates
    matched = candidates[0] if candidates else None
    label = raw.get("label")
    if not isinstance(label, str) and matched is not None:
        label = matched.label
    if not isinstance(label, str) or label not in DEFAULT_QUERY_LABELS:
        warnings.append("relation-endpoint-unknown-label")
        return None

    if matched is not None:
        if raw_text is not None and raw_text != matched.text:
            warnings.append("relation-endpoint-text-mismatch")
            return None
        if raw_start is not None and (raw_start != matched.start or raw_end != matched.end):
            warnings.append("relation-endpoint-offset-mismatch")
            return None
        confidence = _number(raw.get("confidence", raw.get("score")))
        return matched.model_copy(
            update={"confidence": confidence if confidence is not None else matched.confidence}
        )

    return _ground_span(
        raw,
        source,
        default_label=label,
        known_labels=DEFAULT_QUERY_LABELS,
        used_missing_offsets=set(),
        warnings=warnings,
    )


def _normalize_relations(
    grouped: Mapping[str, Any] | list[Any],
    entities: list[EntitySpan],
    source: str,
    warnings: list[str],
) -> list[EntityRelation]:
    if isinstance(grouped, list):
        rows = [
            (item.get("relation") if isinstance(item, Mapping) else None, item) for item in grouped
        ]
    else:
        rows = []
        for relation, values in grouped.items():
            if isinstance(values, list):
                rows.extend((str(relation), value) for value in values)

    normalized: list[EntityRelation] = []
    seen: set[tuple[str, int | None, int | None, int | None, int | None]] = set()
    for relation, raw in rows:
        if relation not in DEFAULT_QUERY_RELATIONS or not isinstance(raw, Mapping):
            warnings.append("relation-not-allowlisted")
            continue
        head = _match_relation_endpoint(raw.get("head"), entities, source, warnings)
        tail = _match_relation_endpoint(raw.get("tail"), entities, source, warnings)
        if head is None or tail is None:
            continue
        if (head.start, head.end) == (tail.start, tail.end):
            warnings.append("relation-identical-endpoints")
            continue
        if head.confidence is None or tail.confidence is None:
            warnings.append("relation-missing-endpoint-confidence")
            continue
        if head.confidence < 0.80 or tail.confidence < 0.80:
            warnings.append("relation-below-endpoint-floor")
            continue
        key = (relation, head.start, head.end, tail.start, tail.end)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            EntityRelation(
                relation=relation,
                head=head,
                tail=tail,
                confidence=min(head.confidence, tail.confidence),
            )
        )
    normalized.sort(
        key=lambda item: (item.head.start or 10**9, item.tail.start or 10**9, item.relation)
    )
    return normalized


def _unique_texts(items: list[EntitySpan]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.text.casefold()
        if key not in seen:
            seen.add(key)
            result.append(item.text)
    return result


def _derive_fields(
    source: str,
    intent: SearchIntent,
    entities: list[EntitySpan],
    relations: list[EntityRelation],
) -> dict[str, Any]:
    ordered = sorted(
        entities, key=lambda item: (item.start if item.start is not None else 10**9, item.text)
    )
    technical = [item for item in ordered if item.label in _TECHNICAL_LABELS]
    other = [item for item in ordered if item.label not in _TECHNICAL_LABELS]
    preserved_terms = _unique_texts(technical + other)

    compared_spans: list[EntitySpan] = []
    for relation in relations:
        if relation.relation == "compares_with":
            compared_spans.extend((relation.head, relation.tail))
    if not compared_spans and intent == "comparison":
        compared_spans = [item for item in ordered if item.label in _COMPARISON_FALLBACK_LABELS]
    compared_entities = _unique_texts(
        sorted(
            compared_spans,
            key=lambda item: (item.start if item.start is not None else 10**9, item.text),
        )
    )

    domain_values = [
        item.text
        for item in ordered
        if item.label in {"language", "platform", "provider", "package", "tool", "topic"}
    ]
    domain_hints: list[str] = []
    seen_hints: set[str] = set()
    for value in [*domain_values, *_langs_from_text(source, domain_values)]:
        key = value.casefold()
        if key not in seen_hints:
            seen_hints.add(key)
            domain_hints.append(value)
        if len(domain_hints) >= 12:
            break

    if _CURRENT_TERMS.search(source):
        time_sensitivity = "current"
    elif _RECENT_TERMS.search(source) or any(item.label == "date" for item in ordered):
        time_sensitivity = "recent"
    elif _HISTORICAL_TERMS.search(source):
        time_sensitivity = "historical"
    else:
        time_sensitivity = "none"

    explicit_comparison = bool(_COMPARISON_TERMS.search(source))
    should_decompose = intent == "comparison" and (
        len(compared_entities) >= 2 or (explicit_comparison and " and " in source.casefold())
    )
    return {
        "preserved_terms": preserved_terms,
        "compared_entities": compared_entities,
        "domain_hints": domain_hints[:12],
        "time_sensitivity": time_sensitivity,
        "should_decompose": should_decompose,
    }


def normalize_query_understanding_response(
    response: Any,
    source: str,
    *,
    confidence_threshold: float,
) -> NormalizedQueryResponse:
    """Validate and normalize a hosted GLiNER2 response."""
    data = _unwrap_response(response)
    warnings: list[str] = []
    used_missing_offsets: set[tuple[int, int]] = set()
    entities: list[EntitySpan] = []
    for default_label, raw in _entity_rows(data["entities"]):
        span = _ground_span(
            raw,
            source,
            default_label=default_label,
            known_labels=DEFAULT_QUERY_LABELS,
            used_missing_offsets=used_missing_offsets,
            warnings=warnings,
        )
        if span is not None:
            entities.append(span)
    entities = postprocess_entities(entities)
    relations = _normalize_relations(data["relations"], entities, source, warnings)

    raw_intent = data["intent"]
    normalized_intent = normalize_intent(raw_intent if isinstance(raw_intent, str) else None)
    canonical_intents = {
        "general",
        "ai_coding_and_infrastructure",
        "digital_humanities",
        "comparison",
        "social_media",
        "news",
    }
    if not isinstance(raw_intent, str) or raw_intent.casefold() not in canonical_intents:
        warnings.append("unknown-intent-label")
    confidence = float(data["confidence"])
    rationale = "gliner2-combined"
    if confidence < confidence_threshold:
        normalized_intent = "general"
        rationale = "gliner2-low-confidence"

    fields = _derive_fields(source, normalized_intent, entities, relations)
    if rationale == "gliner2-low-confidence":
        fields["should_decompose"] = False
    understanding = QueryUnderstandingResult(
        intent=normalized_intent,
        confidence=confidence,
        entities=entities,
        relations=relations,
        rationale=rationale,
        **fields,
    )
    return NormalizedQueryResponse(
        understanding=understanding,
        model_version=data["model_version"].strip(),
        latency_ms=float(data["latency_ms"]),
        warnings=tuple(warnings),
    )
