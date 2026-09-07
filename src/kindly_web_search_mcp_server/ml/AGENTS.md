# ml/

Hosted ML gateway clients. Each module is an async HTTP client for one VPS
service, reached over the SSH tunnel; all share the singleton-client pattern.

## Key Files

| File | Role |
|---|---|
| `embeddings.py` | fastembed-snowflake client (`snowflake/snowflake-arctic-embed-s`, 384-d) on VPS port 8001. `POST /embed` `{texts}` → `{embeddings, model, dimension}`. |
| `gliner_client.py` | Unified-ml GLiNER2 gateway client (VPS port 8000). `/classify` + `/ner` for query understanding, `/extract` for transcripts/content. Singleton via `get_gliner_client`. |

## Rules

- The application never imports `gliner2` or `torch`; inference is performed
  by the configured VPS gateway.
- Query understanding calls `/classify` + `/ner` (no `/v2/query-understanding`
  — that route is not on the container). Entity spans must match exact source
  offsets. Fail open to deterministic `general` only when classify/ner both fail.
- Code-search query enrichment uses `/classify` and `/ner` in parallel through
  `GLiNER2Client.analyze_query_features`; it does not alter the web-search
  intent contract or run relation extraction.
- Content extraction is opt-in via `ENTITY_EXTRACTION_ENABLED` and uses the
  same gateway's `/extract` endpoint.
- `/extract` (`MultiTaskRequest`) accepts flat label-name strings only — dict
  vocabularies are coerced to keys before the wire; descriptions never leave
  the client.
- Entity models, schemas, and post-processing live in `utils/entity.py`;
  chunking and content windowing in `utils/text_chunking.py`.
- Public surface via `ml` package: embedding exports plus `GLiNER2Client`,
  `GatewayAnalysis`, `QueryFeatureAnalysis`, `get_gliner_client`.

## Verification

```bash
uv run ruff check src/kindly_web_search_mcp_server/ml/
uv run python -c "from kindly_web_search_mcp_server.ml import GLiNER2Client, embed_texts"
```