# AGENTS.md - Embeddings

This directory implements the embedding service for semantic search and caching.

## Structure

embeddings/
|-- __init__.py              # Embedding exports
|-- service.py               # HF Space-based embedding service client
|-- models.py                # Embedding model configurations
-- local.py                 # Local embedding fallback (if implemented)

## Service
- Uses Hugging Face Space for embedding generation
- Supports bi-encoder and cross-encoder models
- Used by semantic_cache and rerank pipelines

## Testing
pytest tests/test_embeddings.py -v (if exists)
