# AGENTS.md - A/B Testing Framework

This directory implements the A/B testing framework for search pipeline experiments.

## Structure

ab_testing/
|-- models.py                # ABExperiment, ABVariant, Assignment dataclasses
|-- assignment.py            # get_assigned_variant() with hash-based deterministic bucketing
|-- yaml_loader.py           # load_experiments() / save_experiments() for .kindly/experiments.yaml
|-- wiring.py                # get_ab_overrides(run_key, layer) - returns variant config or None
-- shadow_runner.py         # run_shadow() - fire-and-forget shadow execution

## Wired Pipeline Layers (via get_ab_overrides)

1. **query_understanding** - model, prompt variant, decomposition settings
2. **reranking** - provider, top_k, diversity_weight
3. **provider_weights** - per-provider RRF weight overrides

## Key Concepts

### Shadow Mode
- Variants with shadow: True run in background via asyncio.create_task()
- Does not block production path
- Auto-triggers LLM judge evaluation

### Layer Mutual Exclusion
- Only one running experiment per layer allowed

### CLI Management
web-search-cli experiments list|enable|disable|conclude|stats|create

## Testing
pytest tests/test_ab*.py -v
