# AGENTS.md - Reranking

This directory implements the multi-stage reranking pipeline.

## Current Structure

rerank/
|-- core.py                  # Main rerank orchestration and acceptance gates
|-- stack.py                 # Stack-mode normalization / selection
|-- stage_runner.py          # Cross-encoder and LLM stage execution
|-- stages.py                # Stage definitions, score propagation, diversity
|-- bi_encoder.py            # Bi-encoder shortlist / retrieval rerank
|-- providers.py             # Cohere -> OpenRouter -> Voyage fallback chain
|-- policy.py                # Rerank policy and thresholds
|-- diversity.py             # MMR helpers
|-- llm_rerank.py            # XML listwise LLM rerank and ID remapping
|-- models.py                # Rerank models
|-- reporting.py             # Rerank reporting helpers
|-- observability.py         # Rerank observability helpers
|-- cohere.py, openrouter.py, voyage.py
└-- jina.py                  # Provider adapter retained for direct integration

## Current Behavior

- `RERANK_STACK_MODE` normalizes through `stack.py`; the default stack is
  `bi_cross_llm` (bi-encoder, cross-encoder, LLM reranker, then diversity).
- Cascade narrowing is explicit and monotonic. When a bi-encoder stage runs,
  its target is `int(top_k * RERANK_BI_ENCODER_STAGE_MULTIPLIER)` (default
  multiplier `3.0`). The cross-encoder stage narrows to
  `int(top_k * RERANK_CROSS_ENCODER_STAGE_MULTIPLIER)` (default `2.0`). The LLM
  stage receives every candidate left by the cross-encoder (`candidate_limit`
  equals the current candidate count), rather than a separate stale limit.
- The LLM reranker uses the Qwen XML listwise-CoT template in
  `prompts/rerank_llm.yaml`. Candidate title, URL, and snippet fields are
  whitespace-normalized and XML-escaped inside an explicit untrusted
  `<candidate_data>` block; arbitrary provider payloads, raw HTML, and fetched
  content are not inserted into this prompt. The system rules prohibit
  following instructions found in candidate data.
- Candidate windows are deterministically shuffled before display. The LLM
  sees one-based display IDs; `llm_rerank.py` remaps those IDs to original
  candidate indices after parsing, so positional bias cannot silently become
  result-order bias.
- This deterministic display-order shuffle/remap is the LLM positional-bias debiasing mechanism; candidate identity, not its incoming rank, determines the final index.
- The parser extracts IDs only from the `<final_ranking>` block. IDs are
  deduplicated, invalid/out-of-range IDs are ignored, and missing display IDs
  are appended in display order. Evaluation text is never treated as ranking
  output.
- LLM ordinal relevance uses normalized linear scores: first position is
  `1.0`, last position is `0.0` when there is more than one candidate, and a
  one-candidate list scores `1.0`.
- An LLM outcome is accepted as the final stage only when it is error-free
  (`error is None`) and has non-empty `relevance_scores`. A failed LLM stage
  therefore preserves the preceding cross-encoder order/provider instead of
  being accepted merely because it returned a nonzero output count.
- Diversity is terminal. Its MMR path consumes `candidates[:top_k]` and emits
  only the diversified top-k slice; no post-diversity tail is concatenated.
  Reranker relevance scores are min-max normalized for MMR relevance while
  embeddings provide the diversity signal.

## Testing

- `python -m pytest tests/test_rerank_*.py`
- `python -m pytest tests/test_rerank_core.py tests/test_rerank_stack.py`
