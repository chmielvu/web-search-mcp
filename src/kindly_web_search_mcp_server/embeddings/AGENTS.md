<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-03 | Last verified: 2026-09-03 -->

# AGENTS.md - Embeddings

Embedding subsystem for web-search-mcp.
Unified ML service (`http://127.0.0.1:8000/v1/embeddings`, granite 768d) is the primary
provider, with Hugging Face Inference API available as an alternate/fallback provider.

## Key Files

| File | Role |
|---|---|
| `unified_ml.py` | Primary embedding client for Unified ML (`granite-embedding-311m-multilingual`, 768d) |
| `hf_inference.py` | Alternate/fallback HF Inference API client (768d contract) |
| `__init__.py` | Unified dispatcher routing to primary provider with error handling and fallback |

## Rules

- Unified ML on port 8000 is the default provider (`settings.embedding_provider = "unifiedml"`). Default model `granite-embedding-311m-multilingual`, `settings.embedding_dim = 768`.
- `unified_ml.py` and `hf_inference.py` own singleton clients, connection pooling, validation, circuit breakers, and timeouts.
- For E5 models only, queries get a prefix (`query: <query>` or `Instruct: ...\nQuery: <query>`). Granite is sent unprefixed.
- Public surface: `embed_query`, `embed_texts`, `EMBEDDING_DIM`, `reset_embedding_clients`, `reset_unifiedml_client`, `reset_hf_client`.

## Testing

```bash
uv run pytest tests/test_unified_ml_embeddings.py
uv run pytest tests/test_hf_inference_embeddings.py
```