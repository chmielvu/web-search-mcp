# AGENTS.md - Embeddings

This directory implements the embedding client used by cache and rerank code.

## Current Structure

embeddings/
|-- hf_inference.py          # Hugging Face Inference embeddings client
|-- rate_limiter.py          # Batched / rate-limited embedding wrapper
└── __init__.py              # Public embedding surface

## Current Behavior

- Embeddings are served through Hugging Face Inference
- `hf_inference.py` owns the singleton provider client, validation, circuit breaker,
  and per-call timeouts. The singleton `AsyncInferenceClient` is reused for
  TCP/TLS connection pooling; concurrent calls are allowed because the underlying
  httpx client handles connection multiplexing and the per-caller wrappers
  (`rate_limiter.py`, the Qdrant embedder, and the bi-encoder batch semaphore)
  already throttle their own traffic.

## Notes

- There is no local fallback module in the current tree
- The public surface is `embed_query`, `embed_texts`, `EMBEDDING_DIM`, and
  `BatchLimitedEmbeddings`

## Testing

- `python -m pytest tests/test_hf_inference_embeddings.py`
- `python -m pytest tests/test_semantic_cache_schema.py`
