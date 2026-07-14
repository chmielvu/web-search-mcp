# AGENTS.md - Repository Guide

Kindly Web Search MCP Server is a FastMCP app plus a native Typer CLI.
This repo also contains a separate intent-classifier service, analytics
helpers, and supporting utility packages for caching, reranking,
observability, and content extraction.

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
- [duckdb_data](duckdb_data/AGENTS.md)

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

## DuckDB Data Files

The persistent `.duckdb` databases live under `duckdb_data/` (and a few at the
repo root: `process_logs.duckdb`, `data/blocklist.duckdb`). They use DuckDB's
native **single-writer** format. To read them from any process other than the
running server, open them `READ_ONLY` — never read-write — or you will block on
the writer's lock. See `duckdb_data/AGENTS.md` for the exact CLI/Python
commands and rules.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **web-search-mcp** (7661 symbols, 14350 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/web-search-mcp/context` | Codebase overview, check index freshness |
| `gitnexus://repo/web-search-mcp/clusters` | All functional areas |
| `gitnexus://repo/web-search-mcp/processes` | All execution flows |
| `gitnexus://repo/web-search-mcp/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
