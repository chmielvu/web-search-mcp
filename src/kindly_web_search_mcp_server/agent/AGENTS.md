# AGENTS.md - Agent

This directory contains the experimental agentic research stack.

## Current Structure

agent/
|-- config.py                # Agent runtime configuration
|-- model.py                # Model wrapper / selection helpers
|-- models.py               # Agent data models
|-- prompts.py              # Agent prompt templates
|-- runner.py               # Agent execution loop
|-- mcp.py                  # MCP registration for agent tools
|-- toolset.py              # Tool composition helpers
|-- search_tools.py         # Search-oriented agent tools
|-- content_tools.py        # Content-oriented agent tools
|-- rerank_tools.py         # Reranking tools
|-- academic_tools.py       # Academic research tools
└── knowledge_graph.py      # Ephemeral reasoning graph

## Purpose

- Support multi-step research with LangGraph / LangChain
- Provide an experimental `agentic_web_research` tool surface
- Reuse the main server's search, content, and rerank primitives

## Current Behavior

- The agent stack is experimental and can be disabled independently
- Tool registration happens through `mcp.py`
- `knowledge_graph.py` keeps the reasoning graph lightweight and ephemeral
- External MCP tools can be merged in when configured

## Testing

- `python -m pytest tests/test_agentic_web_research.py`
- `python -m pytest tests/test_agent*.py`
