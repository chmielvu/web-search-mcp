<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-05 | Last verified: 2026-09-05 -->

# AGENTS.md - Reranking

Multi-stage reranking pipeline: BM25, bi-encoder, cross-encoder, RankLLM.

## Key Files

| File | Role |
|---|---|
| `core.py` | Main rerank orchestration and acceptance gates |
| `stage_runner.py` | Cross-encoder and LLM stage execution |
| `stages.py` | Stage definitions, score propagation |
| `diversity.py` | MMR fallback selection over the cross-encoder window |
| `bm25.py` | BM25 scoring utilities |
| `bi_encoder.py` | Bi-encoder shortlist / retrieval rerank |
| `conditional_bi.py` | Conditional bi-encoder gating |
| `providers.py` | Shared response parser and provider fallback chain |
| `llm_rerank.py` | XML listwise RankLLM rerank and pass aggregation |
| `models.py` | Rerank models, stage summaries, and overflow contracts |
| `limits.py` | Candidate and window limits |
| `reporting.py` | Rerank reporting helpers |
| `observability.py` | Asynchronous candidate analytics |

## Pipeline (Monotone Funnel)

```
Provider RRF + BM25 → Bi-encoder (if pool > cross-encoder limit) → Cross-encoder → RankLLM → MMR fallback on RankLLM failure
```

- **Funnel**: 100 candidates → 30 (cross-encoder) → 15 (final output).
- **Merge (single RRF)**: BM25 is computed independently on all raw provider
  results, then fed as an additional ranking signal into a single RRF pass
  alongside provider result lists. No second-stage RRF.
- **Bi-encoder**: Runs only for pools above cross-encoder limit.
- **Cross-encoder**: Cohere `rerank-v4.0-fast` as primary, timeout 5s, fail-fast
  into next provider (OpenRouter → Voyage).
- **RankLLM**: Receives the full labeled query, research goal, intent, caller
  preference, shared ranking hierarchy, and intent policy. It deep-copies each
  pass request, validates each permutation, aggregates valid passes, and reports
  partial success when only some passes fail.
- **Diversity**: Runs only after RankLLM failure, against the cross-encoder
  top-30 window. Embeddings come from the bi-encoder stage when present;
  otherwise a slate-only `embed_query` + `bi_encoder_rank` fallback is used.
  Relevance is taken from the cross-encoder/final/retrieval stage scores, never
  substituted with uniform values. Host caps are relaxed only when necessary
  to fill the requested 15-result slate. Fail-open embedding/MMR errors leave
  the last successful stage order intact.
- **Relevance query**: The shared `query + Research goal` text is used by
  planning, BM25, precomputed embeddings, and conditional bi-encoder scoring.

## Rules

- Provider timeouts: 5s default (fail-fast into next provider).
- SDK retries disabled (`max_retries=0`); fallback belongs to orchestration.
- Cohere/OpenRouter parsers accept partial `top_n` result lists.
- RankLLM uses `gemini-3.5-flash-lite` primary, `gemini-3.1-flash-lite` Google fallback, then OpenRouter.
- MMR is fail-open: embedding outages skip reorder, they do not fail the search.

## Testing

```bash
uv run pytest tests/test_rerank_core.py tests/test_rerank_bi_encoder.py
uv run pytest tests/test_rerank_llm.py tests/test_rerank_prompt.py
uv run pytest tests/test_bm25_rerank.py tests/test_rerank_pipeline_integration.py
uv run pytest tests/test_diversity_mmr.py tests/test_rerank_candidates_diversity.py
```
