<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Last updated: 2026-09-08 | Last verified: 2026-09-08 -->

# AGENTS.md - Reranking

Multi-stage reranking pipeline: BM25, bi-encoder, Voyage cross-encoder, RankLLM (optional).

## Key Files

| File | Role |
|---|---|
| `pipeline.py` | Main rerank orchestration and acceptance gates |
| `models.py` | Rerank models, stage summaries, overflow contracts, and stage limits |
| `bi_encoder.py` | Bi-encoder shortlist / retrieval rerank + conditional bi-encoder gating |
| `cross_encoder.py` | Voyage cross-encoder provider fallback chain + cross stage executor |
| `llm.py` | XML listwise RankLLM rerank, pass aggregation, LLM stage executor |
| `mmr.py` | MMR selection over the cross-encoder window |
| `bm25.py` | BM25 scoring utilities |
| `utils.py` | Shared scoring helpers and ranked-stage application/telemetry |

Rerank analytics (candidate rows, funnel uplift, summary events) live in
`../analytics/rerank_telemetry.py`.

## Pipeline (Monotone Funnel)

```
Shared spine:  RRF (search/merge.py) → Bi-encoder (if pool > 100) → Voyage cross-encoder (100 → 30, recency-blended)
RANKLLM_ENABLED=true:   → RankLLM (30 → 15); MMR rescue on RankLLM failure (30 → 15)
RANKLLM_ENABLED=false:  → MMR terminal (30 → 15)  [designed funnel terminal, not a degradation]
```

- **Funnel**: 100 candidates → 30 (cross-encoder) → 15 (final output).
- **Merge (single RRF)**: BM25 is computed independently on all raw provider
  results, then fed as an additional ranking signal into a single RRF pass
  alongside provider result lists. No second-stage RRF. RRF itself lives in
  `search/merge.py`.
- **Bi-encoder**: Runs only for pools above cross-encoder limit.
- **Cross-encoder**: Voyage `rerank-2.5` primary with `rerank-2.5-lite`
  fallback (`voyage-rerank@voyage` → `voyage-rerank-lite@voyage`, timeout
  `voyage_rerank_timeout`, default 30s). Official `voyageai` client via
  `asyncio.to_thread`, `truncation=True`, `max_retries=0`. Query is the raw
  user text; standing + intent instructions are a separate `instruction`
  argument composed as `{instruction}` then `Query: {query}`. Voyage order is
  kept; recency is recorded, not blended. Pre-flight gate: when
  `VOYAGE_API_KEY` is unset, no call is attempted — the stage fails open
  immediately and MMR becomes the terminal.
- **RankLLM**: Gated by `RANKLLM_ENABLED` (default `false`). Receives the full
  labeled query, research goal, intent, caller preference, shared ranking
  hierarchy, and intent policy. It deep-copies each pass request, validates
  each permutation, aggregates valid passes, and reports partial success when
  only some passes fail.
- **Diversity (MMR)**: λ=0.7, host cap 2. Runs as the designed terminal when
  RankLLM is disabled, or as a rescue after RankLLM failure, against the
  cross-encoder top-30 window. Embeddings come from the bi-encoder stage when
  present; otherwise a slate-only `embed_query` + `bi_encoder_rank` fallback
  is used. Relevance is taken from the cross-encoder/final/retrieval stage
  scores, never substituted with uniform values. Host caps are relaxed only
  when necessary to fill the requested 15-result slate. Fail-open
  embedding/MMR errors leave the last successful stage order intact.
- **Relevance query**: The shared `query + Research goal` text is used by
  planning, BM25, precomputed embeddings, and conditional bi-encoder scoring.

## Rules

- Voyage-only cross-encoder chain; no Cohere/OpenRouter rerank providers.
- Pre-flight `VOYAGE_API_KEY` gate: fail-open with zero HTTP when unset.
- `RANKLLM_ENABLED` (default `false`) skips the LLM stage (summary status
  `skipped`; MMR is the designed terminal). Set `true` to restore RankLLM.
- `voyage_rerank_timeout` (default 30.0) configures the Voyage adapter timeout.
- `VOYAGE_RERANK_FALLBACK_MODEL` (default `rerank-2.5-lite`) is the chain
  second spec. Transport 429/5xx/timeout advance the chain; parse errors do not.
- SDK retries disabled (`max_retries=0`); fallback belongs to orchestration.
- RankLLM uses `gemini-3.5-flash-lite` primary, `gemini-3.1-flash-lite` Google fallback, then OpenRouter.
- MMR is fail-open: embedding outages skip reorder, they do not fail the search.

## Testing

```bash
uv run pytest tests/test_rerank_core.py tests/test_rerank_bi_encoder.py
uv run pytest tests/test_rerank_llm.py tests/test_rerank_prompt.py
uv run pytest tests/test_bm25_rerank.py tests/test_rerank_pipeline_integration.py
uv run pytest tests/test_diversity_mmr.py tests/test_rerank_candidates_diversity.py
```