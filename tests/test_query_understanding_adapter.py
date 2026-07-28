from __future__ import annotations

from kindly_web_search_mcp_server.search.understanding.adapter import (
    normalize_query_understanding_response,
)


def _response(text: str, *, entities=None, relations=None, intent="general", confidence=0.9):
    del text
    return {
        "intent": intent,
        "confidence": confidence,
        "entities": entities if entities is not None else {},
        "relations": relations if relations is not None else {},
        "model_version": "fastino/gliner2-multi-v1",
        "latency_ms": 7.5,
    }


def test_grouped_entities_are_source_grounded_and_distinct_occurrences_survive():
    text = "Use FastAPI and FastAPI"
    result = normalize_query_understanding_response(
        _response(
            text,
            entities={
                "package": [
                    {"text": "FastAPI", "start": 4, "end": 11, "confidence": 0.91},
                    {"text": "FastAPI", "start": 16, "end": 23, "confidence": 0.88},
                ]
            },
        ),
        text,
        confidence_threshold=0.5,
    )

    assert [(entity.start, entity.end) for entity in result.understanding.entities] == [
        (4, 11),
        (16, 23),
    ]
    assert all(
        entity.text == text[entity.start : entity.end] for entity in result.understanding.entities
    )


def test_missing_offsets_are_recovered_but_mismatched_offsets_are_dropped():
    text = "FastAPI v0.100"
    result = normalize_query_understanding_response(
        _response(
            text,
            entities={
                "package": [{"text": "FastAPI", "confidence": 0.91}],
                "version": [{"text": "v0.100", "start": 0, "end": 6, "confidence": 0.9}],
            },
        ),
        text,
        confidence_threshold=0.5,
    )

    assert [
        (entity.text, entity.start, entity.end) for entity in result.understanding.entities
    ] == [("FastAPI", 0, 7)]
    assert "entity-offset-mismatch" in result.warnings


def test_relation_validation_derives_endpoint_confidence_and_filters_low_confidence():
    text = "Compare FastAPI with Starlette"
    result = normalize_query_understanding_response(
        _response(
            text,
            intent="comparison",
            entities={
                "package": [
                    {"text": "FastAPI", "start": 8, "end": 15, "confidence": 0.96},
                    {"text": "Starlette", "start": 21, "end": 30, "confidence": 0.94},
                ]
            },
            relations={
                "compares_with": [
                    {
                        "head": {"text": "FastAPI", "start": 8, "end": 15, "confidence": 0.96},
                        "tail": {"text": "Starlette", "start": 21, "end": 30, "confidence": 0.94},
                    }
                ],
                "uses": [
                    {
                        "head": {"text": "FastAPI", "start": 8, "end": 15, "confidence": 0.79},
                        "tail": {"text": "Starlette", "start": 21, "end": 30, "confidence": 0.95},
                    }
                ],
            },
        ),
        text,
        confidence_threshold=0.5,
    )

    assert len(result.understanding.relations) == 1
    relation = result.understanding.relations[0]
    assert relation.relation == "compares_with"
    assert relation.confidence == 0.94
    assert result.understanding.compared_entities == ["FastAPI", "Starlette"]


def test_unknown_intent_is_general_and_low_confidence_suppresses_decomposition():
    text = "Compare FastAPI with Starlette"
    unknown = normalize_query_understanding_response(
        _response(text, intent="unknown-intent"), text, confidence_threshold=0.5
    )
    low = normalize_query_understanding_response(
        _response(text, intent="comparison", confidence=0.2), text, confidence_threshold=0.5
    )

    assert unknown.understanding.intent == "general"
    assert "unknown-intent-label" in unknown.warnings
    assert low.understanding.intent == "general"
    assert low.understanding.rationale == "gliner2-low-confidence"
    assert low.understanding.should_decompose is False
