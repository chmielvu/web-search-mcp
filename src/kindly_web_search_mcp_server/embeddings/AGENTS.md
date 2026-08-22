<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md - Embeddings

Embedding subsystem for web-search-mcp.
Unified ML ONNX service (`http://127.0.0.1:8000`, 786d) is the primary
provider, with Hugging Face Inference API available as an alternate/fallback provider.

## Key Files

| File | Role |
|---|---|
| `unified_ml.py` | Primary embedding client for the Unified ML service (786d OpenAI/FastEmbed/TEI endpoints) |
| `hf_inference.py` | Alternate/fallback HF Inference API client (786d contract) |
| `__init__.py` | Unified dispatcher routing to primary provider with error handling and fallback |

## Rules

- Unified ML ONNX service on port 8000 is the default embedding provider (`settings.embedding_provider = "unifiedml"`).
- `unified_ml.py` and `hf_inference.py` own singleton clients, connection pooling, validation, circuit breakers, and timeouts.
- For E5 models, queries are automatically formatted with the appropriate prefix (`query: <query>` for standard E5 or `Instruct: ...\nQuery: <query>` for instruct variants).
- Public surface: `embed_query`, `embed_texts`, `EMBEDDING_DIM`, `reset_embedding_clients`, `reset_unifiedml_client`, `reset_hf_client`.

## Testing

```bash
uv run pytest tests/test_unified_ml_embeddings.py
uv run pytest tests/test_hf_inference_embeddings.py
```