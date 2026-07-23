# AGENTS.md - Observability

Shared observability event helpers. Intentionally small.

## Key Files

| File | Role |
|---|---|
| `events.py` | Event helpers / schemas for observability |

## Notes

- Heavier telemetry plumbing lives in `telemetry/` and `utils/observability.py`.
- Analytics storage for observability events lives under `analytics/`.
- Use this package for shared event shapes and import-light helpers.

## Testing

```bash
uv run pytest tests/test_observability_*.py
```