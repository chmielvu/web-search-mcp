# AGENTS.md - Classifier Service

This directory is the separate intent-classifier service used by the search
pipeline when `INTENT_CLASSIFIER_URL` is configured.

## Current Files

- `server.py` - service entrypoint
- `runtime.py` - runtime/bootstrap wiring
- `Dockerfile` - container image for the service
- `requirements.txt` - standalone dependencies for the classifier service

## Purpose

- Serve query-intent classification independently from the MCP server
- Keep the classifier deployment isolated from the main web-search runtime
- Provide a stable HTTP target for `search/settings.py`

## Integration Notes

- The main app reads `INTENT_CLASSIFIER_*` settings from
  `src/kindly_web_search_mcp_server/settings.py`
- If the service contract changes, update the search-side caller and the
  classifier service together

## Testing

- `python -m pytest` for repo-wide validation
- Add service-specific tests alongside runtime changes if this service grows
