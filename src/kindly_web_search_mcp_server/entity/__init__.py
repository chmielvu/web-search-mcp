"""Entity extraction core (pure Python; GLiNER2 is optional/lazy in gliner_client).

Public surface:
- EntitySpan Pydantic model
- DEFAULT_*_LABELS schemas
- chunk_text for offset-aware chunking (reuses windowing boundaries)
- postprocess_entities for validation/dedup/merge/normalize
"""

from __future__ import annotations

from .chunk import chunk_text
from .default_schema import DEFAULT_CONTENT_LABELS, DEFAULT_QUERY_LABELS
from .models import EntitySpan
from .postprocess import postprocess_entities

__all__ = [
    "EntitySpan",
    "DEFAULT_QUERY_LABELS",
    "DEFAULT_CONTENT_LABELS",
    "chunk_text",
    "postprocess_entities",
]
