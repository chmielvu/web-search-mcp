# AGENTS.md - Reranking

This directory implements the multi-stage reranking pipeline.

## Current Structure

rerank/
|-- core.py                  # Main rerank orchestration
|-- stack.py                 # Stack-mode normalization / selection
|-- stage_runner.py          # Stage execution runner
|-- stages.py                # Stage definitions
|-- bi_encoder.py            # Bi-encoder shortlist / retrieval rerank
|-- engines.py               # Rerank engine selection
|-- policy.py                # Rerank policy and thresholds
|-- diversity.py             # Diversity / MMR helpers
|-- llm_rerank.py            # Optional LLM rerank stage
|-- models.py                # Rerank models
|-- reporting.py             # Rerank reporting helpers
|-- observability.py         # Rerank observability helpers
|-- cohere.py                # Cohere adapter
|-- gcp_cloudrun.py          # GCP Cloud Run adapter
|-- jina.py                  # Jina rerank adapter
|-- openrouter.py            # OpenRouter adapter
└── voyage.py                # Voyage adapter

## Current Behavior

- `RERANK_STACK_MODE` normalizes through `stack.py`
- The default repo/runtime path is `bi_cross_llm`
- The pipeline still supports bi-encoder, cross-encoder, and optional LLM
  rerank stages
- The bi-encoder shortlist runs for normal overfetch windows by default. Keep
  candidate embedding text bounded (`RERANK_BI_ENCODER_TEXT_MAX_CHARS`) and
  normal windows in one batch (`RERANK_BI_ENCODER_BATCH_SIZE`) so the stage
  remains functional without stampeding the shared HF embedding client.
- Diversity is applied after candidate scoring/ordering

## Testing

- `python -m pytest tests/test_rerank_*.py`
- `python -m pytest tests/test_rerank_core.py tests/test_rerank_stack.py`
