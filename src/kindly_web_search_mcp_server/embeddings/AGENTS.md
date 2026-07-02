# AGENTS.md - Embeddings

This directory implements the embedding client used by cache and rerank code.

## Current Structure

embeddings/
|-- hf_inference.py          # Hugging Face Inference embeddings client
|-- rate_limiter.py          # Batched / rate-limited embedding wrapper
└── __init__.py              # Public embedding surface

## Current Behavior

- Embeddings are served through Hugging Face Inference
- `hf_inference.py` owns the provider client, validation, and circuit breaker
- `rate_limiter.py` batches and throttles requests so branch fanout does not
  stampede the embedding backend

## Notes

- There is no local fallback module in the current tree
- The public surface is `embed_query`, `embed_texts`, `EMBEDDING_DIM`, and
  `BatchLimitedEmbeddings`

## Testing

- `python -m pytest tests/test_hf_inference_embeddings.py`
- `python -m pytest tests/test_semantic_cache_schema.py`
