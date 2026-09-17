<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-17 | Last verified: 2026-09-17 -->

# AGENTS.md - Index

Write-only remote Qdrant web-results index (dense 384d Arctic + BM25-IDF sparse).

## Key Files

| File | Role |
|---|---|
| `web_results_index.py` | Remote Qdrant web-results writer |
| `bm25_encoder.py` | Sparse BM25 encoder for hybrid indexing |

## Rules

- Write-only — do NOT treat it as the main search surface.
- `WEB_RESULTS_INDEX_ENABLED` gates the write path.
- `QDRANT_SPACE_URL` selects the remote endpoint.
- Collection `web_results_384d`: named dense "dense" (384-d, Cosine, from ml/ Arctic client) + named sparse "sparse" (server-side IDF modifier; locally encoded via `bm25_encoder.encode_bm25`).
- Dense embeddings MUST come from `ml.embed_texts`/`ml.embed_query` (snowflake-arctic-embed-s, 384-dim) — never HF Inference.
- Indexed payloads store native testimony (`engine_rank`, `provider_score`, `source_name`, `source_kind`, `published`, `highlights`, `source_engines`, `origin_adapters`, `answer_kind`) so `search_qdrant` can restore it. Sparse BM25 text is the same title-plus-passages rendering ranking uses.

## Testing

```bash
uv run pytest tests/test_qdrant_search.py
uv run pytest tests/test_index*.py
```