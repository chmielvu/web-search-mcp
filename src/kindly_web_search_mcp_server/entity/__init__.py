"""Pure-Python entity and relation extraction core."""

from __future__ import annotations

from .chunk import chunk_text
from .default_schema import (
    DEFAULT_CONTENT_LABELS,
    DEFAULT_CONTENT_RELATIONS,
    DEFAULT_QUERY_LABELS,
    DEFAULT_QUERY_RELATIONS,
)
from .models import EntityRelation, EntitySpan, RelationMention
from .postprocess import postprocess_entities

__all__ = [
    "EntitySpan",
    "EntityRelation",
    "RelationMention",
    "DEFAULT_QUERY_LABELS",
    "DEFAULT_CONTENT_LABELS",
    "DEFAULT_QUERY_RELATIONS",
    "DEFAULT_CONTENT_RELATIONS",
    "chunk_text",
    "postprocess_entities",
]
