"""Structured output schema for query understanding."""

from __future__ import annotations

QUERY_UNDERSTANDING_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "string", "enum": ["0.2"]},
        "intent": {
            "type": "string",
            "enum": [
                "general",
                "ai_coding_and_infrastructure",
                "digital_humanities",
                "comparison",
                "social_media",
                "news",
            ],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "label": {"type": "string"},
                    "start": {"type": ["integer", "null"]},
                    "end": {"type": ["integer", "null"]},
                    "confidence": {"type": ["number", "null"]},
                },
                "required": ["text", "label", "start", "end", "confidence"],
            },
        },
        "preserved_terms": {
            "type": "array",
            "items": {"type": "string"},
        },
        "compared_entities": {
            "type": "array",
            "items": {"type": "string"},
        },
        "time_sensitivity": {
            "type": "string",
            "enum": ["none", "recent", "current", "historical"],
        },
        "domain_hints": {
            "type": "array",
            "items": {"type": "string"},
        },
        "provider_hints": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "keyword": {"type": "boolean"},
                "neural": {"type": "boolean"},
                "community": {"type": "boolean"},
            },
            "required": ["keyword", "neural", "community"],
        },
        "rewrite_hints": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "style": {"type": "string"},
                "variant_count": {"type": "integer"},
                "preserve_order": {"type": "boolean"},
            },
            "required": ["style", "variant_count", "preserve_order"],
        },
        "rationale": {"type": "string"},
        "should_decompose": {"type": "boolean"},
    },
    "required": [
        "schema_version",
        "intent",
        "confidence",
        "entities",
        "preserved_terms",
        "compared_entities",
        "time_sensitivity",
        "domain_hints",
        "provider_hints",
        "rewrite_hints",
        "rationale",
    ],
}
