# ml/

Hosted ML gateway clients. Each module is an async HTTP client for one VPS
service, reached over the SSH tunnel; all share the singleton-client pattern.

## Key Files

| File | Role |
|---|---|
| `embeddings.py` | fastembed-snowflake client (`snowflake/snowflake-arctic-embed-s`, 384-d) on VPS port 8001. `POST /embed` `{texts}` → `{embeddings, model, dimension}`. |
| `gliner_client.py` | Unified-ml GLiNER2 gateway client (VPS port 8000, server v2). `/classify` (GLiNER2.5 intent classification) + `/ner` for query understanding, `/batch-extract` for transcript batches, `/extract-graph` for typed short-transcript relation graphs, and `/extract` for opt-in content. Singleton via `get_gliner_client`. |

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
- `/classify` request shape: `{text, tasks: {intent: [SearchIntent labels]}}`; response `{results: {intent: {label, confidence, scores[]}}}` — `results` must be unwrapped before parsing (`_parse_classify_task`). Intent labels are derived from `search/intents.py` `INTENT_ALIASES`, never hardcoded.
- `/extract` accepts dict vocabularies `{label: description}` on server v2; the client passes them through so label descriptions reach the GLiNER2 schema builder (improves accuracy). Flat lists also work.
- Server v2 additions usable from the client: `/classify-long`, `/extract-long` (word-chunk scanning), `/batch-extract` (transcript batches), `/extract-graph` (typed short-transcript relation graphs). `/batch-extract` and `/extract-graph` are wired into YouTube analysis.
- `settings.gliner_model` defaults to `fastino/gliner2.5-multi-v1` (the deployed checkpoint); GLINER_MODEL env var overrides.
- Entity models, schemas, and post-processing live in `utils/entity.py`;
  chunking and content windowing in `utils/text_chunking.py`.
- Public surface via `ml` package: embedding exports plus `GLiNER2Client`,
  `GatewayAnalysis`, `QueryFeatureAnalysis`, `get_gliner_client`.

## Verification

```bash
uv run ruff check src/kindly_web_search_mcp_server/ml/
uv run python -c "from kindly_web_search_mcp_server.ml import GLiNER2Client, embed_texts"
```