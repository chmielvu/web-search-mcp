# AGENTS.md - Training

This directory contains the write-only training-data helpers.

## Current Structure

training/
|-- query_understanding_jsonl.py # JSONL sink for query understanding/outcome records
|-- session_state.py             # TTL session state for search-side signals
└── __init__.py                  # Public training helpers

## Purpose

- Emit training records from search events without coupling to analytics reads
- Keep query-understanding records write-only and easy to append
- Maintain short-lived session state for search-side suppression / labeling

## Current Behavior

- `append_query_understanding_record()` writes the understanding snapshot
- `append_query_outcome_record()` writes the observed outcome snapshot
- `SessionStateStore` keeps TTL-based session data in memory

## Testing

- `python -m pytest tests/test_training_jsonl.py`
