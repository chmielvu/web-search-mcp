# AGENTS.md - Repository Guide

Kindly Web Search MCP Server is a FastMCP app plus a native Typer CLI.
This repo also contains a separate intent-classifier service, analytics
helpers, an experimental agentic research module, and supporting utility
packages for caching, reranking, observability, and content extraction.

## Entry Points

- `server.py` - root wrapper for FastMCP launchers
- `src/kindly_web_search_mcp_server/server.py` - main MCP server
- `src/kindly_web_search_mcp_server/__main__.py` - package entrypoint
- `src/kindly_web_search_mcp_server/cli/app.py` - `web-search-cli`
- `src/classifier_service/server.py` - separate intent-classifier service

## Current Architecture

- Search lives in `src/kindly_web_search_mcp_server/search/` and now uses
  `IntentSearchPolicy` plus provider plans. Backend `SearchProfile` routing
  is gone.
- Content lives in `src/kindly_web_search_mcp_server/content/` and resolves
  specialized sources first, then Tavily Map for sitemap discovery, then the
  legacy Crawl4AI sitemap fallback, then Jina / trafilatura fallback.
- Reranking lives in `src/kindly_web_search_mcp_server/rerank/` and defaults
  to the `bi_cross_llm` stack mode.
- Analytics live in `src/kindly_web_search_mcp_server/analytics/` and use
  DuckDB for runs, views, quality metrics, reports, and judge evaluation.
- Caching lives in `src/kindly_web_search_mcp_server/cache/`; exact query
  lookup is in-memory LRU, while page and transcript caches use DuckDB files.
- Agentic research lives in `src/kindly_web_search_mcp_server/agent/` and is
  experimental.
- Tool visibility is controlled by `TOOL_PROFILE`; `TOOL_SEARCH_ENABLED` is
  an opt-in FastMCP search transform.
- `index/` is a write-only remote Qdrant web-results index, not the primary
  retrieval path.

## Package Guides

- [docs](docs/AGENTS.md)
- [tests](tests/AGENTS.md)
- [search](src/kindly_web_search_mcp_server/search/AGENTS.md)
- [content](src/kindly_web_search_mcp_server/content/AGENTS.md)
- [analytics](src/kindly_web_search_mcp_server/analytics/AGENTS.md)
- [cli](src/kindly_web_search_mcp_server/cli/AGENTS.md)
- [agent](src/kindly_web_search_mcp_server/agent/AGENTS.md)
- [rerank](src/kindly_web_search_mcp_server/rerank/AGENTS.md)
- [cache](src/kindly_web_search_mcp_server/cache/AGENTS.md)
- [embeddings](src/kindly_web_search_mcp_server/embeddings/AGENTS.md)
- [entity](src/kindly_web_search_mcp_server/entity/AGENTS.md)
- [index](src/kindly_web_search_mcp_server/index/AGENTS.md)
- [middleware](src/kindly_web_search_mcp_server/middleware/AGENTS.md)
- [prompts](src/kindly_web_search_mcp_server/prompts/AGENTS.md)
- [tools](src/kindly_web_search_mcp_server/tools/AGENTS.md)
- [utils](src/kindly_web_search_mcp_server/utils/AGENTS.md)
- [ab_testing](src/kindly_web_search_mcp_server/ab_testing/AGENTS.md)
- [observability](src/kindly_web_search_mcp_server/observability/AGENTS.md)
- [training](src/kindly_web_search_mcp_server/training/AGENTS.md)
- [classifier_service](src/classifier_service/AGENTS.md)

## Current State Notes

- Search-phase work is past the old profile-routing cleanup and now centers on
  branch/merge/rerank refinement.
- `generate_sitemap` is the public sitemap tool across MCP and CLI, with
  Tavily Map as the primary backend.
- The root CLI surface is `web-search-cli`; there is no `mcp2cli` wrapper.
- `CHANGELOG.md` is part of the source of truth for every code or docs change.
- Update `.agent/CONTINUITY.md` when the repo state or active decisions change.

## Working Commands

- `pytest`
- `python -m pytest tests/test_server.py tests/test_search_orchestrator.py`
- `ruff check src/ tests/`
- `ruff format src/ tests/`
- `web-search-cli schema`
- `web-search-cli doctor`
- `ccc index` after significant code changes

## Edit Rules

- Update the nearest package AGENTS file when changing a subsystem.
- Keep repo-local guidance aligned with the live tree, not historical plans.
- Prefer the current source of truth in code over older notes or plans.
