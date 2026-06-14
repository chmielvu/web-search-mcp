# AGENTS.md - Middleware

This directory contains middleware for the MCP server.

## Structure

middleware/
|-- __init__.py              # Middleware exports
|-- logging.py               # Request/response logging
|-- auth.py                  # Authentication middleware (if implemented)
-- rate_limit.py            # Rate limiting (if implemented)

## Purpose
- Cross-cutting concerns for MCP server
- Request/response logging for observability
- Authentication and authorization
- Rate limiting and quota enforcement

## Testing
pytest tests/test_middleware*.py -v (if exists)
