<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md - Training

Write-only JSONL sink for query understanding training data.

## Key Files

| File | Role |
|---|---|
| `query_understanding_jsonl.py` | JSONL sink for query understanding/outcome records |
| `session_state.py` | TTL session state for search-side signals |

## Rules

- Emit training records from search events without coupling to analytics reads.
- Keep records write-only and easy to append.
- `append_query_understanding_record()` writes the understanding snapshot.
- `append_query_outcome_record()` writes the observed outcome snapshot.
- Understanding records include classifier score vectors, model/endpoint/latency, confidence threshold, decision path, and fallback reason when available.
- Analytics mirrors those decisions in `query_understanding_events`; unlabeled rows are not treated as calibration truth.
- `SessionStateStore` keeps TTL-based session data in memory.

## Testing

```bash
uv run pytest tests/test_training_jsonl.py
```