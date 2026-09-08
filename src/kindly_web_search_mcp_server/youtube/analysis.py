"""Always-on GLiNER2 analysis for normalized YouTube transcripts."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx

from ..ml.gliner_client import get_gliner_client
from ..models import YouTubeTranscriptAnalysis
from ..search.understanding.adapter import normalize_content_entities
from ..utils.entity import (
    DEFAULT_CONTENT_LABELS,
    DEFAULT_CONTENT_RELATIONS,
    _GRAPH_RELATIONS,
    EntityRelation,
    EntitySpan,
    postprocess_entities,
)
from ..utils.text_chunking import chunk_text


def _unwrap_payload(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    result = raw.get("result")
    if isinstance(result, Mapping):
        return result
    results = raw.get("results")
    if isinstance(results, Mapping):
        return results
    return raw


def _structured_data(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = _unwrap_payload(raw)
    for key in ("structured_data", "structured", "data"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return None


def _merge_structured(
    aggregate: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not current:
        return aggregate
    if aggregate is None:
        return dict(current)
    for key, value in current.items():
        if key not in aggregate:
            aggregate[key] = value
        elif isinstance(aggregate[key], list) and isinstance(value, list):
            seen = {repr(item) for item in aggregate[key]}
            aggregate[key].extend(item for item in value if repr(item) not in seen)
        elif aggregate[key] in (None, "", [], {}):
            aggregate[key] = value
    return aggregate


def _find_entity(entities: list[EntitySpan], raw: Any) -> EntitySpan | None:
    if not isinstance(raw, Mapping):
        return None
    start, end = raw.get("start"), raw.get("end")
    text = raw.get("text")
    for entity in entities:
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and entity.start == start
            and entity.end == end
        ):
            return entity
    if isinstance(text, str):
        return next((entity for entity in entities if entity.text == text), None)
    return None


def _parse_relations(raw: Mapping[str, Any], entities: list[EntitySpan]) -> list[EntityRelation]:
    payload = _unwrap_payload(raw)
    grouped = payload.get("relation_extraction") or payload.get("relations", [])
    rows: list[tuple[str, Any]] = []
    if isinstance(grouped, Mapping):
        for relation, values in grouped.items():
            if isinstance(values, list):
                rows.extend((str(relation), value) for value in values)
    elif isinstance(grouped, list):
        rows.extend(("", value) for value in grouped)

    relations: list[EntityRelation] = []
    for relation_name, row in rows:
        if not isinstance(row, Mapping):
            continue
        relation = str(row.get("relation") or row.get("type") or relation_name)
        if relation not in DEFAULT_CONTENT_RELATIONS:
            continue
        head = _find_entity(entities, row.get("head"))
        tail = _find_entity(entities, row.get("tail"))
        if head is None or tail is None:
            continue
        confidence = row.get("confidence", row.get("score"))
        try:
            confidence_value = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence_value = None
        relations.append(
            EntityRelation(
                relation=relation,
                head=head,
                tail=tail,
                confidence=confidence_value,
            )
        )
    return relations


_GRAPH_RELATION_NAMES = frozenset(name for name, _head, _tail in _GRAPH_RELATIONS)


def _parse_graph_relations(
    raw: Mapping[str, Any], entities: list[EntitySpan]
) -> list[EntityRelation]:
    payload = _unwrap_payload(raw)
    rows = payload.get("relations")
    if not isinstance(rows, list):
        return []

    relations: list[EntityRelation] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        relation = str(row.get("relation") or row.get("type") or "")
        if relation not in _GRAPH_RELATION_NAMES:
            continue
        head_raw = row.get("head")
        tail_raw = row.get("tail")
        head = _find_entity(
            entities,
            head_raw if isinstance(head_raw, Mapping) else {"text": head_raw},
        )
        tail = _find_entity(
            entities,
            tail_raw if isinstance(tail_raw, Mapping) else {"text": tail_raw},
        )
        if head is None or tail is None:
            continue
        confidence = row.get("confidence", row.get("score"))
        try:
            confidence_value = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence_value = None
        relations.append(
            EntityRelation(
                relation=relation,
                head=head,
                tail=tail,
                confidence=confidence_value,
            )
        )
    return relations


async def analyze_transcript(text: str) -> YouTubeTranscriptAnalysis:
    """Extract entities and structured data from every transcript, fail-open."""
    started = time.perf_counter()
    if not text.strip():
        return YouTubeTranscriptAnalysis(status="error", warnings=["empty-transcript"])

    client = get_gliner_client()
    chunks = [(0, text)] if len(text) <= 3800 else chunk_text(text, chunk_size=3800, overlap=200)
    entities: list[EntitySpan] = []
    relations: list[EntityRelation] = []
    structured: dict[str, Any] | None = None
    warnings: list[str] = []
    model_version: str | None = None
    batch_results: list[dict[str, Any]] | None = None
    batch_chunk_entities: list[list[EntitySpan]] = []

    for attempt in range(2):
        try:
            batch_results, _request_latency = await client.extract_transcripts_batch(
                [chunk for _offset, chunk in chunks],
                entities=DEFAULT_CONTENT_LABELS,
                relations=DEFAULT_CONTENT_RELATIONS,
            )
            if len(batch_results) != len(chunks):
                raise ValueError(f"expected {len(chunks)} batch results, got {len(batch_results)}")
            break
        except httpx.TimeoutException as exc:
            if attempt == 1:
                batch_results = None
                warnings.append(f"batch-timeout:{type(exc).__name__}")
        except Exception as exc:  # GLiNER2 is analysis-only and must not fail transcription.
            batch_results = None
            warnings.append(f"batch:{type(exc).__name__}:{str(exc)[:160]}")
            break

    if batch_results is not None:
        for (offset, chunk), raw in zip(chunks, batch_results):
            try:
                if not isinstance(raw, Mapping):
                    raise ValueError("batch result is not an object")
                model_version = str(
                    raw.get("model_version") or raw.get("model") or client._model_name
                )
                chunk_entities = normalize_content_entities(raw, chunk)
                shifted_entities = [
                    entity.model_copy(
                        update={
                            "start": entity.start + offset if entity.start is not None else None,
                            "end": entity.end + offset if entity.end is not None else None,
                        }
                    )
                    for entity in chunk_entities
                ]
                entities.extend(shifted_entities)
                batch_chunk_entities.append(chunk_entities)
                if len(chunks) > 1:
                    relations.extend(_parse_relations(raw, chunk_entities))
                structured = _merge_structured(structured, _structured_data(raw))
            except Exception as exc:  # GLiNER2 is analysis-only and must not fail transcription.
                warnings.append(f"chunk-{offset}:{type(exc).__name__}:{str(exc)[:160]}")

    if batch_results is not None and len(chunks) == 1:
        try:
            graph = await client.extract_relation_graph(
                text,
                entities=DEFAULT_CONTENT_LABELS,
            )
            graph_entities = normalize_content_entities(graph, text)
            graph_relations = _parse_graph_relations(graph, graph_entities)
            if graph_entities and graph_relations:
                entities = graph_entities
                relations = graph_relations
            elif batch_chunk_entities:
                relations = _parse_relations(batch_results[0], batch_chunk_entities[0])
        except Exception as exc:  # Graph extraction is an optional precision pass.
            warnings.append(f"graph-{type(exc).__name__}")
            if batch_chunk_entities:
                relations = _parse_relations(batch_results[0], batch_chunk_entities[0])

    entities = postprocess_entities(entities)
    status = "success" if not warnings else "partial"
    if warnings and not entities and structured is None:
        status = "error"
    return YouTubeTranscriptAnalysis(
        status=status,
        entities=entities,
        relations=relations,
        structured_data=structured,
        model_version=model_version,
        chunk_count=len(chunks),
        latency_ms=(time.perf_counter() - started) * 1000.0,
        warnings=warnings,
    )
