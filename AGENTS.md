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
python -m pytest tests/test_server.py tests/test_page_content_resolver.py tests/test_tool_descriptions.py tests/test_search_router.py tests/test_query_rewrite.py tests/test_search_orchestrator.py
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
- `cli.py` — wrapper CLI with `start-mcp-server` subcommand
- `server.py` — FastMCP server exposing 6 tools: `web_search`, `get_content`, `gemini_search`, `perplexity_search`, `youtube_transcript`, `youtube_search`

### Search pipeline (`search/`)
- `orchestrator.py` — coordinates rewrite → multi-provider search → merge → rerank
- `searxng.py`, `tavily.py`, `brave.py`, `jina.py`, `pollinations.py` — provider implementations
- `merge.py` — RRF (k=60) merge across providers
- `query_rewrite.py` — Mistral-backed query expansion/variant generation
- `query_policy.py` + `query_policy_resolver.py` + `query_policy_hf.py` — intent classification and rewrite mode selection (bypass/light_rewrite/decompose)

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
All `KINDLY_*` env vars documented there. Key ones:
- Search providers: `SEARXNG_BASE_URL`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, `JINA_API_KEY`
- `GITHUB_TOKEN` — recommended for better GitHub Issue extraction
- `KINDLY_BROWSER_EXECUTABLE_PATH` — Chrome/Chromium/Edge path (optional, auto-detected)
- `KINDLY_SEMANTIC_CACHE_ENABLED`, `KINDLY_RERANKING_ENABLED`, `KINDLY_QUERY_REWRITE_ENABLED`

## Key Patterns

### Adding a new content resolver
1. Create module in `content/` with `parse_x_url()` and `fetch_x_markdown()`
2. Add import and handler stage in `content/resolver.py`
3. Write unit tests in `tests/test_x.py` mocking the API

### Adding a new search provider
1. Create module in `search/` with `search_provider(query, num_results, http_client, diagnostics)` returning normalized results
2. Register in `search/__init__.py` and `search/orchestrator.py`
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

Per `.agent/CONTINUITY.md`, ongoing refactor phases:
- Phase 1–3: Complete (lightweight search, orchestrator extraction, query policy)
- Phase 4: Merge/diversity/rerank refinement (next)
- Separate track: GitHub GraphQL tuning in `plans/GraphQL-tuning.md`

## Environment Setup

Required: at least one search provider env var.
```powershell
$env:SEARXNG_BASE_URL="http://localhost:8080"  # or TAVILY_API_KEY, BRAVE_API_KEY, JINA_API_KEY
$env:GITHUB_TOKEN="..."  # recommended
```

Optional for advanced features:
```powershell
$env:MISTRAL_API_KEY="..."  # query rewrite
$env:KINDLY_GEMINI_API_KEY="..."  # gemini_search grounding
$env:POLLINATIONS_API_KEY="..."  # perplexity_search (Perplexity Sonar via Pollinations)
$env:KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL="..."  # YouTube transcript proxy (for cloud IPs)
```

## Documentation Index

- [CHANGELOG.md](./CHANGELOG.md) — Version history and changes
- [CONTRIBUTING.md](./CONTRIBUTING.md) — Development guidelines
- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) — System architecture and data flows
- [docs/CONFIGURATION.md](./docs/CONFIGURATION.md) — Environment variables and settings
- [docs/GETTING-STARTED.md](./docs/GETTING-STARTED.md) — Quick start guide
- [docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md) — Development patterns and workflows
- [docs/TESTING.md](./docs/TESTING.md) — Testing guide and mock patterns