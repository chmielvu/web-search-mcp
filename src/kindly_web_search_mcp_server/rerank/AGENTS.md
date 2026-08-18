# AGENTS.md - Reranking

Multi-stage reranking pipeline: BM25, bi-encoder, cross-encoder, RankLLM.

## Key Files

| File | Role |
|---|---|
| `core.py` | Main rerank orchestration and acceptance gates |
| `stage_runner.py` | Cross-encoder and LLM stage execution |
| `stages.py` | Stage definitions, score propagation, diversity (MMR) |
| `bm25.py` | BM25 scoring utilities |
| `bi_encoder.py` | Bi-encoder shortlist / retrieval rerank |
| `conditional_bi.py` | Conditional bi-encoder gating |
| `providers.py` | Cohere → OpenRouter → Voyage fallback chain |
| `llm_rerank.py` | XML listwise RankLLM rerank and ID remapping |
| `models.py` | Rerank models and providers |
| `cohere.py`, `openrouter.py`, `voyage.py`, `jina.py` | Cross-encoder provider adapters |
| `limits.py` | Candidate and window limits |
| `reporting.py` | Rerank reporting helpers |

## Pipeline (Monotone Funnel)

```
Provider RRF + BM25 → Bi-encoder (if pool > cross-encoder limit) → Cross-encoder → RankLLM
```

- **Funnel**: 100 candidates → 30 (cross-encoder) → 15 (final output).
- **Merge (single RRF)**: BM25 is computed independently on all raw provider
  results, then fed as an additional ranking signal into a single RRF pass
  alongside provider result lists. No second-stage RRF.
- **Bi-encoder**: Runs only for pools above cross-encoder limit.
- **Cross-encoder**: Cohere `rerank-v4.0-fast` as primary, timeout 5s, fail-fast
  into next provider (OpenRouter → Voyage).
- **RankLLM**: Receives the full labeled query, research goal, intent, caller
  preference, shared ranking hierarchy, and intent policy. It preserves the
  Gemini → Gemini → OpenRouter fallback chain, shuffles candidate order before
  each bounded listwise call to reduce positional bias, validates complete
  permutations, enforces the total timeout budget, and drains canceled tasks.
- **Diversity**: Conditional MMR triggered by similarity or host-overflow.
  Reconstructs the untouched tail — candidate identities never silently dropped.
- **Relevance query**: The shared `query + Research goal` text is used by
  planning, BM25, precomputed embeddings, and conditional bi-encoder scoring.

## Rules

- Provider timeouts: 5s default (fail-fast into next provider).
- SDK retries disabled (`max_retries=0`); fallback belongs to orchestration.
- Cohere/OpenRouter parsers accept partial `top_n` result lists.
- RankLLM uses `gemini-3.5-flash-lite` primary, `gemini-3.1-flash-lite` Google fallback, then OpenRouter.
- `scripts/rerank_eval_diversity.py` is stale — imports removed `rerank.diversity`.

## Testing

```bash
uv run pytest tests/test_rerank_core.py tests/test_rerank_bi_encoder.py
uv run pytest tests/test_rerank_llm.py tests/test_rerank_prompt.py
uv run pytest tests/test_bm25_rerank.py tests/test_rerank_pipeline_integration.py
```
