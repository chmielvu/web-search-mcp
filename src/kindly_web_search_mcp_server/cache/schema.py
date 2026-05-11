"""LanceDB schema definitions for semantic cache."""

from __future__ import annotations

import pyarrow as pa

SEMANTIC_CACHE_TABLE_NAME = "semantic_cache_hf_inference_BAAI_bge_m3_1024"

# Semantic cache schema with BGE-M3 1024-dim embeddings.
SEMANTIC_CACHE_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("query_hash", pa.string()),
        pa.field("query_text", pa.string()),
        pa.field("answer_json", pa.string()),
        pa.field("provider_key", pa.string()),
        pa.field("content_type", pa.string()),
        pa.field("created_at", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), 1024)),
    ]
)
