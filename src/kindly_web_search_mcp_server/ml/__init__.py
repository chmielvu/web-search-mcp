"""ML subsystem: hosted gateway clients (embeddings + GLiNER2) and the offline mode router."""

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
from .tf_idf_router import ROUTER_ID as MODE_ROUTER_ID
from .tf_idf_router import ModeRoute, route_quick_mode

__all__ = [
    "EMBEDDING_DIM",
    "MODE_ROUTER_ID",
    "EmbeddingAPIError",
    "EmbeddingDimensionError",
    "EmbeddingTimeoutError",
    "GLiNER2Client",
    "GatewayAnalysis",
    "ModeRoute",
    "QueryFeatureAnalysis",
    "embed_query",
    "embed_texts",
    "get_gliner_client",
    "reset_client",
    "route_quick_mode",
]
