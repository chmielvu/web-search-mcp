# AGENTS.md - A/B Testing Framework

This directory implements the search-experiment A/B testing layer.

## Current Structure

ab_testing/
|-- models.py                # Experiment / variant data models
|-- assignment.py            # Deterministic bucketing
|-- yaml_loader.py           # YAML load/save helpers
|-- wiring.py                # Runtime wiring / override lookup
└── shadow_runner.py         # Shadow execution runner

## Wired Layers

1. `query_understanding`
2. `reranking`

## Current Behavior

- Shadow variants run out-of-band and should not block the production path
- Only one active experiment per layer should be running at a time
- The CLI manages experiments through `web-search-cli experiments ...`

## Testing

- `python -m pytest tests/test_ab_*.py`
