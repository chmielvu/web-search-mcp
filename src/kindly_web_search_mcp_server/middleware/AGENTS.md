<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md - Middleware

FastMCP middleware stack: rate limits, query guidance, tool protection.

## Key Files

| File | Role |
|---|---|
| `expensive_tool_protection.py` | First-call protection for expensive tools |
| `query_guidance.py` | Result-aware guidance and suggestions |
| `rate_limits.py` | Differentiated token-bucket rate limits (cheap vs expensive) |
| `session_tracking.py` | Session-scoped counters and TTL state |

## Rules

- `grok_search` is protected by query-quality gating.
- Cheap and expensive tool groups use separate rate-limit buckets.
- Guidance is appended to tool results when middleware can improve next agent action.
- This package is about quality steering and throttling, **not** auth.
- Session tracking is lightweight and TTL-based.

## Testing

```bash
uv run pytest tests/test_middleware*.py
uv run pytest tests/test_agent_steering_middleware.py
```