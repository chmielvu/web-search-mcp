# AGENTS.md - Utils

This directory contains shared utilities used across the codebase.

## Current Structure

utils/
|-- async_helpers.py         # Async helpers and task wrappers
|-- background_tasks.py      # Fire-and-forget task tracking
|-- diagnostics.py           # Diagnostics helpers and env masking
|-- duckdb_log_handler.py    # DuckDB process-log handler
|-- http_client.py           # Shared HTTP client lifecycle
|-- logging.py               # Logging configuration
|-- observability.py         # Observability event helpers
|-- paths.py                 # Path helpers and defaults
|-- public_output.py         # Public response serialization
|-- task_scope.py            # Deadline-scoped task management for fan-out workers
|-- singleflight.py          # Request coalescing
|-- snippet_normalizer.py    # Snippet cleanup helpers
└── structured_logging.py    # Structured logging helpers

## Purpose

- Keep cross-cutting helpers out of feature packages
- Centralize logging, HTTP client reuse, observability, and output shaping
- Provide small reusable primitives like singleflight and snippet cleanup

## Testing

- `python -m pytest tests/test_async_helpers.py tests/test_scripts_env_loader.py`
