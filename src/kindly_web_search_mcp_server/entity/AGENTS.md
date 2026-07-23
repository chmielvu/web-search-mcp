# AGENTS.md - Entity

Entity extraction for query handling and content analysis.

## Key Files

| File | Role |
|---|---|
| `gliner_client.py` | Optional lazy GLiNER2 HTTP client |
| `models.py` | `EntitySpan`, `EntitySet` models |
| `chunk.py` | Offset-preserving chunking for long text |
| `overlap.py` | Entity overlap scoring for rerank |
| `default_schema.py` | Default entity label schemas |
| `postprocess.py` | Validation, deduplication, normalization |

## Rules

- GLiNER2 is optional and lazily loaded — never force it as a dependency.
- `chunk.py` preserves global offsets for long text.
- `postprocess.py` is the last stage before returning entity spans.
- Public surface: `EntitySpan`, default schemas, chunking, post-processing.

## Testing

```bash
uv run pytest tests/test_entity_*.py
uv run pytest tests/test_entity_response_fields.py
```