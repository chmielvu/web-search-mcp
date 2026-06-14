# AGENTS.md - Index

This directory contains the web search results indexing functionality.

## Structure

index/
|-- __init__.py              # Index exports
|-- web_results_index.py     # Remote Qdrant index for web search results (write-only)
|-- bm25_encoder.py          # BM25 sparse vector encoding

## Purpose
- Indexes final search results into remote Qdrant (HF Space)
- Hybrid dense + sparse vectors (COSINE + BM25)
- Write-only; used for analytics and future retrieval

## Configuration
- WEB_RESULTS_INDEX_ENABLED env var
- QDRANT_SPACE_URL for remote endpoint
- HF_TOKEN for authentication

## Testing
pytest tests/test_index*.py -v (if exists)
