# AGENTS.md - LLM

This directory contains LLM integration and provider abstractions.

## Structure

llm/
|-- __init__.py              # LLM exports
|-- providers.py             # LLM provider abstractions (OpenAI, Anthropic, etc.)
|-- prompts.py               # Shared prompt templates
-- utils.py                 # LLM utility functions

## Purpose
- Abstracts LLM provider differences
- Manages prompt templates for various tasks
- Handles provider-specific configurations

## Testing
pytest tests/test_llm*.py -v (if exists)
