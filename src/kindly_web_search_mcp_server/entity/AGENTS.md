# AGENTS.md - Entity

This directory contains entity extraction and resolution for search results.

## Structure

entity/
|-- __init__.py              # Entity exports
|-- extractor.py             # Entity extraction from search results
|-- resolver.py              # Entity resolution and linking
-- models.py                # Entity data models

## Purpose
- Extracts named entities from search results
- Resolves entities to knowledge base entries
- Provides entity-aware result enrichment

## Testing
pytest tests/test_entity*.py -v
