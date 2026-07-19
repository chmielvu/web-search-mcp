# AGENTS.md - Reranking

This directory implements the multi-stage reranking pipeline.

## Current Structure

rerank/
|-- core.py                  # Main rerank orchestration and acceptance gates
|-- stage_runner.py          # Cross-encoder and LLM stage execution
|-- stages.py                # Stage definitions, score propagation, diversity
|-- bi_encoder.py            # Bi-encoder shortlist / retrieval rerank
|-- conditional_bi.py        # Conditional bi-encoder gating
|-- providers.py             # Cohere -> OpenRouter -> Voyage fallback chain
|-- llm_rerank.py            # XML listwise LLM rerank and ID remapping
|-- models.py                # Rerank models
|-- reporting.py             # Rerank reporting helpers
|-- observability.py         # Rerank observability helpers
|-- bm25.py                  # BM25 scoring utilities
|-- limits.py                # Candidate and window limits
|-- cohere.py, openrouter.py, voyage.py
|-- jina.py                  # Provider adapter retained for direct integration

## Current Behavior

- The normal pipeline preserves the complete merged candidate pool. It uses
  provider-consensus RRF followed by BM25 RRF before reranking.
- The bi-encoder runs only for candidate pools above the configured
  cross-encoder limit. Cohere `rerank-v4.0-fast` reranks the full surviving
  head and emits raw scores for the calibrated conditional gate.
- RankLLM uses the repository prompt template with strict complete-permutation
  validation per sliding window and across the final candidate set. OpenRouter
  is attempted first; Gemini is the sequential fallback. Each transport call
  is bounded and has SDK retries disabled.
- RankLLM receives only the normalized query. The cross-encoder receives the
  research goal separately and constructs its own query input.
- Diversity is conditional. Similarity and host-overflow triggers run MMR over
  the LLM candidate window, then reconstruct the untouched tail so candidate
  identities are never silently dropped.
- Stage telemetry records bi-encoder skip/run details, raw Cohere score
  statistics, the accepted RankLLM endpoint/model, diversity trigger evidence,
  and the final reranker provider/model.
- Calibration and replay tooling: `scripts/rerank_eval_calibration.py`,
  `scripts/rerank_eval_capture.py`, `scripts/rerank_eval_common.py`, and
  `scripts/rerank_eval_fusion.py` are available. The diversity replay script
  (`scripts/rerank_eval_diversity.py`) is stale — it imports removed
  `rerank.diversity` and requires migration. Corresponding test
  `tests/test_rerank_pipeline_eval.py` was removed.

## Testing

- `python -m pytest tests/test_rerank_core.py tests/test_rerank_settings.py tests/test_rerank_bi_encoder.py tests/test_rerank_llm.py tests/test_rerank_prompt.py tests/test_rerank_engines.py tests/test_rerank_eval_metrics.py tests/test_rerank_candidate_writes.py tests/test_rerank_pipeline_integration.py`
