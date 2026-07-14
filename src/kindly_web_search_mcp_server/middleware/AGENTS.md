# AGENTS.md - Middleware

This directory contains the FastMCP middleware stack.

## Current Structure

middleware/
|-- expensive_tool_protection.py # First-call protection for expensive tools
|-- query_guidance.py            # Result-aware guidance and suggestions
|-- rate_limits.py               # Differentiated token-bucket rate limits
|-- session_tracking.py          # Session-scoped counters and TTL state
└── __init__.py                  # Public middleware exports

## Current Behavior

- `grok_search` is protected by query-quality gating
- Cheap and expensive tool groups use separate rate-limit buckets
- Guidance is appended to tool results when the middleware can improve the next
  agent action

## Notes

- This package is about quality steering and throttling, not auth
- Session tracking is lightweight and TTL-based

## Testing

- `python -m pytest tests/test_middleware*.py`
- `python -m pytest tests/test_agent_steering_middleware.py`
