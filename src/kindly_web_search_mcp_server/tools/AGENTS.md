# AGENTS.md - Tools

This directory contains tool metadata and visibility helpers.

## Current Structure

tools/
|-- catalog.py               # Tool catalog and metadata helpers
|-- profiles.py              # Tool profile application / visibility
└── __init__.py              # Public tool metadata surface

## Current Behavior

- Actual MCP tool implementations live in `server.py` and the feature
  packages, not in this directory
- `profiles.py` controls which tools are exposed for each `TOOL_PROFILE`
- `catalog.py` centralizes the metadata used by the server and CLI help

## Tool Surface

- Core MCP tools are defined in `src/kindly_web_search_mcp_server/server.py`
- Visibility is profile-based, not hard-coded in the tool call sites

## Testing

- `python -m pytest tests/test_tool_descriptions.py tests/test_server.py`
