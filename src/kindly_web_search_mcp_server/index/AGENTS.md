# AGENTS.md - Index

Write-only remote Qdrant web-results index (dense 1024d + BM25 sparse).

## Key Files

| File | Role |
|---|---|
| `web_results_index.py` | Remote Qdrant web-results writer |
| `bm25_encoder.py` | Sparse BM25 encoder for hybrid indexing |

## Rules

- Write-only — do NOT treat it as the main search surface.
- `WEB_RESULTS_INDEX_ENABLED` gates the write path.
- `QDRANT_SPACE_URL` selects the remote endpoint.
- Uses hybrid dense (1024d) + sparse representations for future retrieval experiments.

## Testing

```bash
uv run pytest tests/test_qdrant_search.py
uv run pytest tests/test_index*.py
```