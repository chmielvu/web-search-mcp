<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md - Entity

Entity extraction for query handling and content analysis.

## Key Files

| File | Role |
| `gliner_client.py` | Hosted VPS GLiNER2 HTTP gateway for query/content extraction |
| `models.py` | `EntitySpan` and `EntityRelation` models |
| `chunk.py` | Offset-preserving chunking for long text |
| `overlap.py` | Entity overlap scoring for rerank |
| `default_schema.py` | Default entity/relation label schemas |
| `postprocess.py` | Source-grounded validation, deduplication, normalization |

## Rules

- The application never imports `gliner2` or `torch`; inference is performed by the configured VPS gateway.
- Query understanding uses one `/v2/query-understanding` request and fails open to deterministic `general` when unavailable.
- Code-search query enrichment uses the deployed lightweight `/classify` and `/ner` endpoints in parallel through `GLiNER2Client.analyze_query_features`; it does not alter the web-search intent contract or run relation extraction.
- Content extraction is opt-in via `ENTITY_EXTRACTION_ENABLED` and uses the same gateway's `/extract` endpoint.
- `chunk.py` preserves global offsets for long text.
- Preserve label descriptions in `/extract` payloads; they are part of GLiNER2's entity schema, not cosmetic metadata.
- Chunk boundaries may honor paragraph/sentence cuts only when doing so does not leave uncovered source gaps.
- `postprocess.py` is the last stage before returning entity spans and retains exact source surface text.
- Public surface: `EntitySpan`, `EntityRelation`, default schemas, chunking, post-processing, and `get_gliner_client`.

## Testing

```bash
uv run pytest tests/test_entity_*.py
uv run pytest tests/test_entity_response_fields.py
```
