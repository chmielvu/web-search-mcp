# AGENTS.md - Utils

This directory contains shared utilities used across the codebase.

## Structure

utils/
|-- __init__.py              # Utils exports
|-- structured_logging.py    # Structured logging setup
|-- http_client.py           # Shared HTTP client with retries
|-- text.py                  # Text processing utilities
|-- time.py                  # Time utilities
-- validation.py            # Input validation helpers

## Purpose
- Common utilities to avoid duplication
- Consistent logging, HTTP, text processing
- Shared validation and helper functions

## Testing
pytest tests/test_utils*.py -v (if exists)
