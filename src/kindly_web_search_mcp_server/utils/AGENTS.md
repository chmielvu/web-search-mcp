# utils/

Cross-cutting pure helpers shared by search, content, middleware, and tools.
No FastMCP imports; no network I/O except `ml/embeddings.py` (HTTP client to
the fastembed service).

## Modules

| File | Role |
| `text_clean.py` | Query ingress cleaning (`clean_query`, `repair_unicode`). |
| `query_pipeline.py` | Query shaping: lingua language gate, wordsegment glue segmentation (free role only), parse-once `QueryFeatures`, per-role rendering (`shape_for_branch`). SERP roles keep boolean/engine operators; Sentry-style boolean cleanup after op stripping. Stage 3a is a general non-overlap union (`_CLASS_PRECEDENCE` tiebreak); splice is offset-safe (H11/H12). |
| `query_understanding.py` | Deterministic GLiNER-outage fallback: comparison extraction, time sensitivity. No keyword-set intent flipping. (Embedding-kNN intent tier removed 2026-09-09 — zero callers.) |
| `guidance_messages.py` | Cause-aware guidance strings for web_search middleware (verbatim move from heuristics). |
| `entity.py` | Entity contracts merged from former `entity/` package: `EntitySpan`/`EntityRelation` models, default label/relation schemas, `postprocess_entities` (validation, dedup, overlap merge). Pure Python; no gateway import. |
| `text_chunking.py` | `slice_content`/`ContentWindow`/`WindowedContent` (merged from former `content/windowing.py`) + `chunk_text` (from former `entity/chunk.py`), sharing public `find_boundary_index`. |
| `gliner_client.py` (`ml/gliner_client.py`) | Unified-ml GLiNER2 gateway client (VPS `127.0.0.1:8000` via SSH tunnel): `/classify` + `/ner` for query understanding, `/extract` for transcripts/content. Singleton via `get_gliner_client`; `ml` re-exports the contract. |
| `embeddings.py` (`ml/embeddings.py`) | fastembed-snowflake client (`snowflake/snowflake-arctic-embed-s`, 384-d, VPS `127.0.0.1:8001`, SSH tunnel). `POST /embed` `{texts}` → `{embeddings, model, dimension}`. No circuit breaker. |
| `duckdb_log_handler.py` / `sqlite_log_handler.py` | Batched process-log sinks with UTC timestamps. |
| `youtube_urls.py` | YouTube URL identity: `parse_youtube_url`, `looks_like_channel_target`, `YouTubeTarget`, `YouTubeError`. Pure stdlib; moved from removed `youtube/` package. |

## Rules

- Public surface: `embed_query`, `embed_texts`, `EMBEDDING_DIM`, `reset_client` (from `ml`), plus `GLiNER2Client`, `GatewayAnalysis`, `QueryFeatureAnalysis`, `get_gliner_client`.
- Exports re-exported: `ml` package exposes the embedding and GLiNER2 gateway contracts formerly at `embeddings` and `entity`.
- `settings.embedding_endpoint_url` defaults to `http://127.0.0.1:8001`; `settings.intent_classifier_url` defaults to `http://127.0.0.1:8000`. Override both for tunnels.
- `MAX_TOKEN_LEN = 24` is wordsegment's hard `LIMIT`; tokens >24 chars never segment.
- `_MIN_PROTECTED_TOKENS` starts empty; add tokens only with fixture evidence.
- Snippet normalization lives in `../utils/public_output.py` (merged 2026-09-09; `snippet_normalizer.py` deleted).
- `_BARE_LANG_WORDS` (query_pipeline): bare-word language whitelist — excludes `go`/`r`/`cs`; add entries only with fixture evidence (mirrors `_MIN_PROTECTED_TOKENS` policy).
- Jina frontmatter parsing is capture-group based (no `[3:-5]` slice); the Jina fetch stage (`content/stages.py`) strips the envelope before classification.
- `utils/diagnostics.py` was deleted 2026-09-09 (zero importers).
- `entity.py` post-processing preserves exact source surface text; chunk boundaries never skip source text.