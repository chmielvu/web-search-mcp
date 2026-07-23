# AGENTS.md - Classifier Service

Standalone intent-classifier service for the search pipeline.

## Key Files

| File | Role |
|---|---|
| `server.py` | Service entrypoint |
| `runtime.py` | Runtime/bootstrap wiring |
| `Dockerfile` | Container image |
| `requirements.txt` | Standalone dependencies |

## Rules

- Serves query-intent classification independently from the MCP server.
- Keeps classifier deployment isolated from main web-search runtime.
- Provides stable HTTP target for `search/settings.py` via `INTENT_CLASSIFIER_*` settings.
- If service contract changes, update the search-side caller and classifier together.

## Testing

```bash
uv run pytest  # repo-wide validation
```
