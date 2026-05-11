"""LanceDB schema definitions for semantic cache."""

from __future__ import annotations

import re

import pyarrow as pa

from ..settings import settings


def _slugify_model_name(model_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", model_name.lower()).strip("_")
    if not slug:
        return "unknown_model"
    return slug[:80]


def get_semantic_cache_table_name(
    model_name: str | None = None,
    embedding_dim: int | None = None,
) -> str:
    """Build a semantic cache table name for the active embedding config."""
    resolved_model = model_name or settings.hf_embedding_model
    resolved_dim = embedding_dim or settings.embedding_dim
    return f"semantic_cache_{_slugify_model_name(resolved_model)}_{resolved_dim}"


def get_semantic_cache_schema(embedding_dim: int | None = None) -> pa.Schema:
    """Build a semantic cache schema for the active embedding dimension."""
    resolved_dim = embedding_dim or settings.embedding_dim
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("query_hash", pa.string()),
            pa.field("query_text", pa.string()),
            pa.field("answer_json", pa.string()),
            pa.field("provider_key", pa.string()),
            pa.field("content_type", pa.string()),
            pa.field("created_at", pa.string()),
            pa.field("embedding", pa.list_(pa.float32(), resolved_dim)),
        ]
    )


SEMANTIC_CACHE_TABLE_NAME = get_semantic_cache_table_name()
SEMANTIC_CACHE_SCHEMA = get_semantic_cache_schema()
