# AGENTS.md - Entity

This directory contains the entity extraction core used by query handling and
content analysis.

## Current Structure

entity/
|-- chunk.py                 # Offset-preserving chunking helpers
|-- default_schema.py        # Default label schemas
|-- gliner_client.py         # Optional lazy GLiNER2 client
|-- models.py                # Entity span models
|-- overlap.py               # Overlap / span merging helpers
|-- postprocess.py           # Validation, deduplication, normalization
└── __init__.py              # Public entity surface

## Purpose

- Extract grounded entity spans from query or content text
- Keep the core pure Python while making GLiNER2 optional and lazy
- Normalize, deduplicate, and merge entity spans before downstream use

## Current Behavior

- `chunk.py` preserves global offsets for long text
- `postprocess.py` is the last stage before returning entity spans
- The public surface is `EntitySpan`, the default schemas, chunking, and
  post-processing helpers

## Testing

- `python -m pytest tests/test_entity_*.py`
- `python -m pytest tests/test_entity_response_fields.py`
