# AGENTS.md - Embeddings

HF Inference embedding client used by cache and rerank.

## Key Files

| File | Role |
|---|---|
| `hf_inference.py` | HF Inference API embedding client (model: `intfloat/multilingual-e5-large-instruct`, 1024d) |
| `rate_limiter.py` | Batched / rate-limited embedding wrapper |

## Rules

- Embeddings served through Hugging Face Inference API.
- `hf_inference.py` owns the singleton provider client, validation, circuit breaker,
  and per-call timeouts.
- Singleton `AsyncInferenceClient` is reused for TCP/TLS connection pooling.
- No local fallback module in current tree.
- Public surface: `embed_query`, `embed_texts`, `EMBEDDING_DIM`, `BatchLimitedEmbeddings`.

## Testing

```bash
uv run pytest tests/test_hf_inference_embeddings.py
uv run pytest tests/test_semantic_cache_schema.py
```