# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Kindly Web Search MCP Server — multi-provider web search (SearXNG/Tavily/Brave/Jina) with RRF merge, specialized content extraction, and semantic caching. Designed for AI coding assistants (Codex, Codex, Cursor, etc.).

## Commands

### Run the MCP server
```bash
uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server \
  kindly-web-search-mcp-server start-mcp-server
```

For HTTP transport (testing/debugging):
```bash
uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server \
  kindly-web-search-mcp-server start-mcp-server --http --port 8000
```

### Run tests
```bash
pytest
```

Focused test slice (core search contract):
```bash
python -m pytest tests/test_server.py tests/test_page_content_resolver.py tests/test_tool_descriptions.py tests/test_profile_resolution.py tests/test_prompt_registry.py tests/test_query_understanding.py tests/test_provider_plan.py tests/test_training_jsonl.py tests/test_entity_response_fields.py tests/test_search_orchestrator.py tests/test_search_router.py
```

Single test file:
```bash
python -m pytest tests/test_searxng_unit.py -v
```

### Lint/format
```bash
ruff check src/
ruff format src/
```

## Architecture

### Entry points
- `server.py` — MCP server entry point for `start-mcp-server`
- `cli/` — native Typer package for `web-search-cli`
- `server.py` — FastMCP server exposing 6 tools: `web_search`, `get_content`, `gemini_search`, `perplexity_search`, `youtube_transcript`, `youtube_search`

### Search pipeline (`search/`)
- `pipeline.py` — coordinates understanding → profiled rewrite → multi-provider search → merge → rerank
- `searxng.py`, `tavily.py`, `brave.py`, `jina.py`, `pollinations.py` — provider implementations
- `merge.py` — RRF (k=60) merge across providers
- `understanding/resolver.py` — LLM-backed query understanding and intent resolution
- `pipeline_builders.py` — prompt registry integration plus rewrite variant construction
- `provider_plan.py` / `provider_options.py` / `provider_call.py` — profile-derived provider weights, allow-lists, and provider arguments
- `query_policy.py` — lightweight rewrite policy model used by the live pipeline response

### Content resolution (`content/resolver.py`)
Staged fallback pipeline:
1. StackExchange API (full thread: question + answers + comments)
2. GitHub Issues API (GraphQL)
3. GitHub Discussions API (GraphQL)
4. Wikipedia API (MediaWiki Action API)
5. arXiv (Atom API + PDF → Markdown)
6. HTTP extraction (trafilatura)
7. Universal HTML (nodriver headless browser for JS-heavy sites)

### Scraping (`scrape/`)
- `universal_html.py` — nodriver-based browser extraction
- `chromium_pool.py` — pooled browser instances for reuse
- `http_extract.py` — trafilatura primary, no browser

### Caching (`cache/`)
- `query_cache.py` — exact query cache (SQLite-backed, deterministic)
- `semantic_cache.py` — LanceDB-backed semantic similarity cache (embedding-based fuzzy match)
- `page_cache.py` — URL → page_content cache

### Embeddings & Reranking (`embeddings/`, `rerank/`)
- HF Space-based embedding service
- Bi-encoder + cross-encoder reranking pipeline

### Settings (`settings.py`)
All `*` env vars documented there. Key ones:
- Search providers: `SEARXNG_BASE_URL`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, `JINA_API_KEY`
- `GITHUB_TOKEN` — recommended for better GitHub Issue extraction
- `BROWSER_EXECUTABLE_PATH` — Chrome/Chromium/Edge path (optional, auto-detected)
- `RERANKING_ENABLED`, `QUERY_REWRITE_CASCADE_TIMEOUT_SECONDS`, `CLASSIFIER_TIMEOUT_SECONDS`, `QUERY_UNDERSTANDING_JSONL_ENABLED`
- `AB_TESTING_ENABLED`, `AB_CONFIG_PATH`, `AB_SHADOW_MODE_DEFAULT`, `AB_ASSIGNMENT_CACHE_TTL_SECONDS` — A/B testing configuration
- `JUDGE_EVALUATION_ENABLED`, `JUDGE_MODEL`, `JUDGE_TIMEOUT_SECONDS` — LLM judge configuration

### Analytics & Search Quality (`analytics/`)
- `duckdb_store.py` — 21 DuckDB tables + insert functions for the search quality pipeline
- `views.py` — 13 human-readable SQL views for analytics queries
- `quality_metrics.py` — `compute_search_quality()` per-run quality scoring
- `summaries.py` — `refresh_summary_tables()` daily aggregate refresh
- `judge_prompt.py` — LLM judge prompt construction and score parsing
- `judge_runner.py` — Fire-and-forget LLM judge evaluation after production response
- `judge_calibration.py` — Judge score normalization/calibration

**Pipeline data flow** (all joined by `run_key`):
1. `search_runs` → `query_understanding` → `query_rewrites` (input side)
2. `provider_calls` → `provider_candidates` (per-provider results)
3. `merged_candidates` (RRF merge output)
4. `rerank_stages` → `rerank_candidates` (multi-stage reranking)
5. `final_results` (output)
6. `search_quality_scores` (computed quality metrics)
7. `judge_evaluations` (LLM-as-judge scoring)

Full schema reference: [docs/DuckDB_schema.md](./docs/DuckDB_schema.md)

### A/B Testing (`ab_testing/`)
- `models.py` — `ABExperiment`, `ABVariant`, `Assignment` dataclasses
- `assignment.py` — `get_assigned_variant()` with hash-based deterministic bucketing
- `yaml_loader.py` — `load_experiments()` / `save_experiments()` for `.kindly/experiments.yaml`
- `wiring.py` — `get_ab_overrides(run_key, layer)` — returns variant config or None
- `shadow_runner.py` — `run_shadow()` — fire-and-forget shadow execution

**Wired pipeline layers** (via `get_ab_overrides`):
1. `query_understanding` — model, prompt variant, decomposition settings
2. `reranking` — provider, top_k, diversity_weight
3. `provider_weights` — per-provider RRF weight overrides

**Shadow mode:** When a variant has `shadow: True`, the override is applied to a background `asyncio.create_task()` that does not block the production path. Shadow runs auto-trigger LLM judge evaluation.

**Layer mutual exclusion:** Only one running experiment per layer is allowed.

**CLI management:**
```bash
web-search-cli experiments list
web-search-cli experiments enable <experiment_id>
web-search-cli experiments disable <experiment_id>
web-search-cli experiments conclude <experiment_id> --winner <variant_key>
web-search-cli experiments stats <experiment_id>
web-search-cli experiments create [--config <json>]
```

## Key Patterns

### Adding a new content resolver
1. Create module in `content/` with `parse_x_url()` and `fetch_x_markdown()`
2. Add import and handler stage in `content/resolver.py`
3. Write unit tests in `tests/test_x.py` mocking the API

### Adding a new search provider
1. Create module in `search/` with `search_provider(query, num_results, http_client, diagnostics)` returning normalized results
2. Register in `search/__init__.py` and `search/provider_config.py`, then add profile hooks in `search/provider_plan.py` if the provider needs intent-specific weights or arguments
3. Add env var config in `settings.py` if needed

### Testing mocks
Tests patch under `kindly_web_search_mcp_server.*` namespace:
```python
with patch("kindly_web_search_mcp_server.content.resolver.parse_stackexchange_url", ...):
```

For async: use `AsyncMock` with `unittest.IsolatedAsyncioTestCase`.

### Tool contracts
- `web_search` returns **lightweight results only** (title, link, snippet) — no page_content
- `get_content` returns **LLM-ready markdown** for a single URL
- `perplexity_search` returns **AI-synthesized answers with citations** (uses Perplexity Sonar)
- `gemini_search` returns **grounded answers with citations** (uses Gemini + Google Search)
- `youtube_transcript` returns **video transcripts** with optional translation/formatting
- `youtube_search` returns **YouTube video results** via SearXNG YouTube engine
- Separation is intentional: search discovers, fetch extracts, AI search synthesizes

## Changelog

**All changes to this project must be documented in [CHANGELOG.md](./CHANGELOG.md).**

When making modifications:
1. Add entries under `[Unreleased]` section
2. Follow Keep a Changelog format (Added, Changed, Fixed, Deprecated, Removed, Security)
3. Include PR/issue references when applicable
4. Move entries to version section on release

## Current Development Focus

Per `.agent/CONTINUITY.md`:
- Phase 1–3: Complete (lightweight search, orchestrator extraction, query policy)
- Phase 4: Merge/diversity/rerank refinement (next)
- Search quality analytics: Complete — 21 DuckDB tables, 13 views, LLM judge, A/B testing framework
- Separate track: GitHub GraphQL tuning in `plans/GraphQL-tuning.md`

## Native CLI

The generated mcp2cli wrapper surface has been removed. The intended CLI is a first-party Typer application named `web-search-cli`, designed under `plans/web-search-cli-native-typer-design-2026-06-07.md`, with JSON-first output, structured errors, and no mcp2cli compatibility layer.

Scaffolded commands currently available:

```powershell
web-search-cli schema
web-search-cli doctor
web-search-cli getskill
web-search-cli getskill --dev
web-search-cli reference tools
web-search-cli reference external-tools
```

Operational commands:
- `web-search-cli experiments list|enable|disable|conclude|stats|create` — A/B experiment management
- `web-search-cli analytics query` — run analytics queries against DuckDB
- `web-search-cli analytics report` — run named reports



grafana otel token: REDACTED
cc token: REDACTED



## AI Coding Tools

### Code Graph CLI (codegraph)

`codegraph` is installed and available for dependency graph visualization.

**Purpose:** Creates a graph of code to show dependencies between code entities (methods, classes, etc.). Useful for understanding call chains and impact analysis before making changes.

**Install:**
```bash
pip install codegraph
```

**Usage:**
```bash
# Generate dependency graph for a directory
codegraph src/kindly_web_search_mcp_server/search --object-only

# Generate interactive HTML graph
codegraph src/kindly_web_search_mcp_server --output codegraph.html

# Start from a specific file
codegraph src/kindly_web_search_mcp_server/search/pipeline.py --file-path src/kindly_web_search_mcp_server/search/pipeline.py --distance 2

# Export graph data to CSV
codegraph src/kindly_web_search_mcp_server/search --csv graph.csv
```

**Note:** Current version may have parsing issues with some async function patterns. If `codegraph` crashes with `AttributeError: 'NoneType' object has no attribute 'file'`, use `ccc` instead for semantic code search.

### CocoIndex Code CLI (ccc)

`ccc` (CocoIndex Code) is the primary codebase indexing and semantic search tool for this repo.

**Purpose:** Indexes the codebase into a graph-backed semantic search system. Use it to find relevant code by intent, not just filename.

**Commands:**
```bash
# Initialize project (if needed)
ccc init

# Create/update index for the codebase
ccc index

# Show project status
ccc status

# Semantic search across codebase
ccc search "where is query understanding implemented?"

# Reset project databases
ccc reset

# Check system health
ccc doctor

# Run as MCP server
ccc mcp
```

**Configuration:** The index is configured in `.cocoindex_code/settings.yml`. Current settings only index `**/*.py` and `**/*.toml` files to keep context focused on source code and configuration.

**Why use ccc:**
- Finds code by semantic intent, not just keyword matches
- Maintains a graph of code relationships for dependency-aware retrieval
- Avoids context pollution from irrelevant files
- Works as both CLI and MCP server for AI coding agents

**Workflow:**
1. Run `ccc index` after significant code changes
2. Use `ccc search "<intent>"` to find relevant code before editing
3. Use `ccc status` to verify index health
4. Use `ccc mcp` when you want Claude Code to access the codebase graph directly
