# AGENTS.md - CLI (web-search-cli)

This directory contains the native Typer CLI application for the web-search-mcp server.

## Structure

cli/
|-- __init__.py              # CLI exports
|-- app.py                   # Main Typer application entry point
|-- commands/                # Command implementations
|   |-- __init__.py
|   |-- schema.py            # schema command
|   |-- doctor.py            # doctor command
|   |-- getskill.py          # getskill command
|   |-- reference.py         # reference commands
|   |-- experiments.py       # A/B experiment management
|   -- analytics.py         # analytics query/report commands
-- services/                # Shared services
    |-- __init__.py
    -- mcp_client.py        # MCP client for server communication

## Commands

### Scaffolded Commands
- web-search-cli schema
- web-search-cli doctor
- web-search-cli getskill [--dev]
- web-search-cli reference tools
- web-search-cli reference external-tools

### Operational Commands
- web-search-cli experiments list|enable|disable|conclude|stats|create
- web-search-cli analytics query
- web-search-cli analytics report

## Design Principles
- JSON-first output for agent consumption
- Structured errors with error codes
- No mcp2cli compatibility layer
- Designed per plans/web-search-cli-native-typer-design-2026-06-07.md

## Testing
pytest tests/test_cli*.py -v
