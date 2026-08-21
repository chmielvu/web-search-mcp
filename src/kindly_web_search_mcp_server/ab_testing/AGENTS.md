<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md - A/B Testing Framework

Search-experiment A/B testing with deterministic bucketing and shadow execution.

## Key Files

| File | Role |
|---|---|
| `models.py` | Experiment/variant data models |
| `assignment.py` | Deterministic user-to-variant bucketing |
| `yaml_loader.py` | YAML experiment config load/save |
| `wiring.py` | Runtime wiring / override lookup |
| `shadow_runner.py` | Shadow mode execution (out-of-band) |

## Wired Layers

1. `query_understanding`
2. `reranking`

## Rules

- Shadow variants run out-of-band and must NOT block the production path.
- `shadow_runner.run_shadow` normalizes the retrieval-facing `top_k` keyword
  to the legacy shadow callable's `top_n` keyword when needed.
- Only one active experiment per layer at a time.
- CLI manages experiments via `uv run web-search-cli experiments ...`.

## Testing

```bash
uv run pytest tests/test_ab_*.py
```
