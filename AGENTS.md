# AGENTS.md — Kindly Web Search MCP Server

FastMCP server + Typer CLI for multi-provider web search, content extraction,
reranking, analytics, and AI-grounded answers.

## Tech Stack

- **Python ≥3.12**, Hatchling build
- **FastMCP ≥3.4** — MCP server framework
- **Typer ≥0.16 + Rich** — CLI (`uv run web-search-cli`)
- **DuckDB** — analytics, page cache, transcript cache, blocklist
- **OpenTelemetry + Arize Phoenix** — LLM tracing and metrics
- **Pydantic** — all input/output contracts
- **19 search providers** — Brave, Tavily, SearXNG, Jina, SerpAPI, Serper,
  BrightData, LangSearch, DuckDuckGo, DeGoog, Reddit, HackerNews, GitHub,
  Gemma, Grok, Qdrant, Telegram, Composio

## Exact Commands

```bash
# Install
uv sync

# Run all tests
uv run pytest

# Lint & format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Run the CLI (ALL CLI invocations MUST use this form)
uv run web-search-cli doctor
uv run web-search-cli schema
uv run web-search-cli reference tools
uv run web-search-cli search web --query "..." --objective "..."
uv run web-search-cli search quick --search-query "..." --objective "..."
uv run web-search-cli content <url>
uv run web-search-cli links <url>
uv run web-search-cli ai <query>
uv run web-search-cli youtube search <query>
uv run web-search-cli youtube transcript <video-id>
uv run web-search-cli sitemap generate <url>
uv run web-search-cli analytics query
uv run web-search-cli analytics report <name>
uv run web-search-cli experiments list|create|enable|disable|conclude|stats
uv run web-search-cli server
```

## Package Guides (Progressive Disclosure)

| Navigate to | When modifying |
|---|---|
| `src/kindly_web_search_mcp_server/search/AGENTS.md` | Search pipeline (planning, retrieval, ranking, 19 providers) |
| `src/kindly_web_search_mcp_server/content/AGENTS.md` | Content fetching pipeline (resolvers, extraction stages) |
| `src/kindly_web_search_mcp_server/rerank/AGENTS.md` | Multi-stage reranking (BM25, bi-encoder, cross-encoder, RankLLM) |
| `src/kindly_web_search_mcp_server/analytics/AGENTS.md` | DuckDB analytics, quality metrics, LLM judge pipeline |
| `src/kindly_web_search_mcp_server/cli/AGENTS.md` | CLI commands and services |
| `src/kindly_web_search_mcp_server/tools/AGENTS.md` | MCP tool metadata, profiles, catalog |
| `src/kindly_web_search_mcp_server/cache/AGENTS.md` | In-memory LRU + DuckDB page/transcript caches |
| `src/kindly_web_search_mcp_server/llm/AGENTS.md` | LLM routing (Cerebras → Groq → HF → Vercel fallback) |
| `src/kindly_web_search_mcp_server/middleware/AGENTS.md` | FastMCP middleware (rate limits, guidance, protection) |
| `src/kindly_web_search_mcp_server/prompts/AGENTS.md` | Prompt templates and registry |
| `src/kindly_web_search_mcp_server/embeddings/AGENTS.md` | HF Inference embedding client |
| `src/kindly_web_search_mcp_server/index/AGENTS.md` | Write-only Qdrant web-results index |
| `src/kindly_web_search_mcp_server/entity/AGENTS.md` | Entity extraction (GLiNER2, chunking, overlap) |
| `src/kindly_web_search_mcp_server/ab_testing/AGENTS.md` | A/B testing framework |
| `src/kindly_web_search_mcp_server/observability/AGENTS.md` | Observability event helpers |
| `src/kindly_web_search_mcp_server/telemetry/AGENTS.md` | OpenTelemetry instrumentation |
| `src/kindly_web_search_mcp_server/training/AGENTS.md` | Write-only JSONL training data sink |
| `src/kindly_web_search_mcp_server/utils/AGENTS.md` | Cross-cutting helpers (HTTP, logging, async) |
| `src/classifier_service/AGENTS.md` | Standalone intent-classifier service |
| `tests/AGENTS.md` | Test organization and conventions |
| `docs/AGENTS.md` | Human-readable documentation |
| `duckdb_data/AGENTS.md` | DuckDB database inventory and read-only access |

## Boundaries

- **ALWAYS do:** Update the nearest package AGENTS.md when changing a subsystem.
  Document all changes in `CHANGELOG.md` under `[Unreleased]`.
  Update `.agent/CONTINUITY.md` with decisions, code changes, and discoveries.
- **ASK FIRST** before modifying `settings.py` (env var config affects all
  subsystems) or `pyproject.toml` (dependency changes).
- **NEVER** modify files in `.agent/`, `.gitnexus/`, or `.venv*`.
  NEVER commit API keys or secrets.
  NEVER blame external APIs for timeouts — root cause is always local code.

## Git Workflow

- Update `CHANGELOG.md` in the same commit as code changes.
- Reference `.agent/CONTINUITY.md` for active development history and decisions.
- Before editing any symbol, run `impact({target: "symbolName", direction: "upstream"})` to assess blast radius.
- Before committing, run `detect_changes()` to verify changes affect only expected symbols.

## DuckDB Data Files

Persistent `.duckdb` databases use DuckDB's **single-writer** format.
Read from any process other than the running server **MUST** be `READ_ONLY`.
See `duckdb_data/AGENTS.md` for inventory, CLI exploration commands, and
read-only access patterns.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **web-search-mcp** (8279 symbols, 13632 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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
