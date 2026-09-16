"""ML subsystem: hosted gateway clients (embeddings + GLiNER2)."""

from .embeddings import (
    EMBEDDING_DIM,
    EmbeddingAPIError,
    EmbeddingDimensionError,
    EmbeddingTimeoutError,
    embed_query,
    embed_texts,
    reset_client,
)
from .gliner_client import (
    GatewayAnalysis,
    GLiNER2Client,
    QueryFeatureAnalysis,
    get_gliner_client,
)

__all__ = [
    "EMBEDDING_DIM",
    "EmbeddingAPIError",
    "EmbeddingDimensionError",
    "EmbeddingTimeoutError",
    "GLiNER2Client",
    "GatewayAnalysis",
    "QueryFeatureAnalysis",
    "embed_query",
    "embed_texts",
    "get_gliner_client",
    "reset_client",
]
