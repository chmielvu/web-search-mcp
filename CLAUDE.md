# CLAUDE.md

This repo uses [AGENTS.md](AGENTS.md) as the single source of truth for workspace guidance.
7. Universal HTML (Crawl4AI/Playwright headless browser for JS-heavy sites)

### Scraping (`scrape/`)
- `universal_html.py` — Crawl4AI-based browser extraction (Stage 7 fallback)
- `crawl4ai_worker.py` — subprocess entry point for Crawl4AI/Playwright (MCP-stdio-safe)
- `chromium_pool.py` — deprecated (Crawl4AI manages its own browser lifecycle)
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

- `gemini_search` returns **grounded answers with citations** (uses Gemini + Google Search)
- `youtube_transcript` returns **video transcripts** with optional translation/formatting
- `youtube_search` returns **YouTube video results** via YouTube Data API v3 (when `GOOGLE_API_KEY` set) or SearXNG fallback
- `generate_semantic_sitemap` returns **structured heading hierarchy per page** from crawled documentation sites (uses Crawl4AI AsyncUrlSeeder + AsyncWebCrawler); optional llms.txt generation
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
$env:AI_GATEWAY_API_KEY="..."  # query understanding / rewrite workers
$env:QUERY_UNDERSTANDING_MODEL="amazon/nova-micro"
$env:CEREBRAS_REWRITE_MODEL="cerebras/gpt-oss-120b"
$env:GROQ_REWRITE_MODEL="groq/gpt-oss-120b"
$env:VERCEL_REWRITE_MODEL="groq/gpt-oss-20b"
$env:GEMINI_API_KEY="..."  # gemini_search grounding

$env:YOUTUBE_TRANSCRIPT_PROXY_URL="..."  # YouTube transcript proxy (for cloud IPs)
```

## Documentation Index

- [CHANGELOG.md](./CHANGELOG.md) — Version history and changes
- [CONTRIBUTING.md](./CONTRIBUTING.md) — Development guidelines
- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) — System architecture and data flows
- [docs/CONFIGURATION.md](./docs/CONFIGURATION.md) — Environment variables and settings
- [docs/GETTING-STARTED.md](./docs/GETTING-STARTED.md) — Quick start guide
- [docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md) — Development patterns and workflows
- [docs/TESTING.md](./docs/TESTING.md) — Testing guide and mock patterns

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
