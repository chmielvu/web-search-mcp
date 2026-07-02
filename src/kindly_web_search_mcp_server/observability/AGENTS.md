# AGENTS.md - Observability

This directory is intentionally small and holds shared observability events.

## Current Structure

observability/
|-- events.py                # Event helpers / schemas for observability
└── AGENTS.md                # Package guidance

## Notes

- The heavier telemetry plumbing lives in `src/kindly_web_search_mcp_server/telemetry.py`
  and `src/kindly_web_search_mcp_server/utils/observability.py`
- Analytics storage for observability events lives under `analytics/`

## Use This Package For

- Shared event shapes and helpers that need to stay import-light
- Small observability primitives that are reused by server and middleware code

## Testing

- `python -m pytest tests/test_observability_*.py`
