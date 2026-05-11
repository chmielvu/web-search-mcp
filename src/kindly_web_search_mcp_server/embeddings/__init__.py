"""Embeddings module for web-search-mcp."""

from .hf_inference import EMBEDDING_DIM, embed_query, embed_texts
from .rate_limiter import BatchLimitedEmbeddings

__all__ = [
    "embed_texts",
    "embed_query",
    "EMBEDDING_DIM",
    "BatchLimitedEmbeddings",
]
