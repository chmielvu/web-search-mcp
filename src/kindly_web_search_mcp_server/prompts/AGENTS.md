# AGENTS.md - Prompts

This directory contains prompt builders, registry helpers, and model-specific
prompt families.

## Current Structure

prompts/
|-- registry.py              # Prompt registry and lookup
|-- builders.py              # Prompt-building helpers
|-- query_understanding.py   # Query understanding prompts
|-- query_rewrite.py         # Query rewrite prompts
|-- rerank.py                # Reranking prompts
|-- rerank_llm.py            # LLM rerank prompts / config
|-- entity_extraction.py     # Entity extraction prompts
|-- provider_gemini.py       # Gemini provider prompt family
|-- provider_grok.py         # Grok provider prompt family
|-- models.py                # Prompt-related models
└── rerank_llm.yaml          # LLM rerank prompt config

## Current Behavior

- Prompt registry is versioned and used by the search pipeline
- Prompt families are separated by task and provider rather than one giant blob
- Prompt changes should stay aligned with the tests that exercise the registry

## Testing

- `python -m pytest tests/test_prompt_registry.py`
- `python -m pytest tests/test_query_understanding.py tests/test_rerank_llm.py`
