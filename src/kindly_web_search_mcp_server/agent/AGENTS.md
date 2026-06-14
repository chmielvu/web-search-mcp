# AGENTS.md - Agent

This directory contains agent-related functionality for the MCP server.

## Structure

agent/
|-- __init__.py              # Agent exports
|-- runner.py                # Agent execution runner
|-- tools.py                 # Agent tool definitions
-- prompts.py               # Agent system prompts

## Purpose
- Implements agentic workflows for complex search tasks
- Manages multi-step search and synthesis
- Integrates with MCP tool ecosystem

## Testing
pytest tests/test_agent*.py -v (if exists)
