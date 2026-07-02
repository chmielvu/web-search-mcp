# AGENTS.md - Index

This directory contains the write-only remote results index.

## Current Structure

index/
|-- bm25_encoder.py          # Sparse encoder for hybrid indexing
|-- web_results_index.py     # Remote Qdrant web-results writer
└── __init__.py              # Public index surface

## Purpose

- Write final search results into the remote Qdrant space
- Use hybrid dense + sparse representations for future retrieval experiments
- Keep indexing separate from the primary search path

## Current Behavior

- `WEB_RESULTS_INDEX_ENABLED` gates the write path
- `QDRANT_SPACE_URL` selects the remote endpoint
- The index is write-only; do not treat it as the main search surface

## Testing

- `python -m pytest tests/test_qdrant_search.py`
- `python -m pytest tests/test_index*.py`
