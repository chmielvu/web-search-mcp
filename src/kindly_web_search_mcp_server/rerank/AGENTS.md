# AGENTS.md - Reranking

This directory implements the multi-stage reranking pipeline for search results.

## Structure

rerank/
|-- __init__.py              # Rerank exports
|-- reranker.py              # Main reranking orchestrator
|-- bi_encoder.py            # Bi-encoder (retrieval) reranking
|-- cross_encoder.py         # Cross-encoder (precision) reranking
-- models.py                # Rerank model configurations

## Pipeline Stages

1. **Bi-encoder stage** - Fast retrieval reranking using embeddings
2. **Cross-encoder stage** - Precision reranking with full attention
3. **Diversity weighting** - Optional diversity penalty for similar results

## Configuration
- RERANKING_ENABLED env var controls activation
- Top-k and diversity_weight configurable per profile
- A/B testing can override rerank provider and parameters

## Testing
pytest tests/test_rerank*.py -v
