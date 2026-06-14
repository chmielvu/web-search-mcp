# AGENTS.md - Prompts

This directory contains prompt templates and registry for the search pipeline.

## Structure

prompts/
|-- __init__.py              # Prompt exports
|-- registry.py              # Prompt registry with versioning
|-- query_understanding.py   # Query understanding prompts
|-- rewrite.py               # Query rewrite prompts
|-- rerank.py                # Reranking prompts
-- synthesis.py             # Answer synthesis prompts

## Prompt Registry
- Centralized prompt management with versioning
- Supports A/B testing of prompt variants
- Templates loaded at startup, cached in memory

## Testing
pytest tests/test_prompt_registry.py -v
