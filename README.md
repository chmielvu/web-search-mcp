# Kindly Web Search MCP Server

Multi-provider web search MCP server for AI coding assistants (Codex, Cursor, Claude Code, etc.). Aggregates results from 15+ search providers using RRF merge, extracts content from 10+ source types, and provides AI-synthesized answers with citations.

## Installation

### From source

```bash
git clone https://github.com/chmielvu/web-search-mcp
cd web-search-mcp
uv sync
```

`uv sync` installs the runtime dependencies and the `dev` dependency group. The package is not published on PyPI under a project name matching this repository, so install from source.

Installed console scripts:

| Script | Purpose |
|---|---|
| `mcp-server`, `mcp-web-search`, `web-search-mcp` | Start the MCP server |
| `web-search-cli` | JSON-first CLI (`doctor`, `schema`, `search web`, `content`, `youtube`, …) |

## Quick Start

### 1. Configure at least one search provider

```bash
# SearXNG (self-hosted, no API key)
export SEARXNG_BASE_URL="http://localhost:8080"

# Or Tavily
export TAVILY_API_KEY="tvly-..."

# Or Brave
export BRAVE_API_KEY="BSA..."

# Or Jina
export JINA_API_KEY="jina_..."

# Or Parallel AI (for quick_web_search)
export PARALLEL_API_KEY="pk_..."
```

### 2. Run the MCP server

```bash
# Stdio transport (default, for AI coding assistants)
mcp-server

# Streamable HTTP transport (for testing/debugging)
mcp-server --http --port 8000

# Other transports and bind options
mcp-server --transport stdio|sse|streamable-http --host 127.0.0.1 --port 8000
```

### 3. Add to your MCP client config

```json
{
  "mcpServers": {
    "kindly-web-search": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/web-search-mcp", "run", "mcp-server"]
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `web_search` | Multi-provider web search with RRF merge and rerank |
| `fetch` | Fetch one or many URLs with typed routing, metadata, links, summaries, and bounded continuation |
| `crawl_web` | Bounded Crawl4AI site traversal with typed targets, browser interactions, Markdown structure counts, and persisted outputs |
| `gemini_search` | AI-synthesized answers via Gemini + Google Search |
| `grok_search` | AI-synthesized answers via Grok/xAI |
| `academic_search` | Search across academic databases (arXiv, PubMed, Semantic Scholar, OpenAlex, CrossRef) |
| `generate_sitemap` | Map a site's URL hierarchy and page structure |
| `code_search` | Search public source code, implementation examples, technical documentation, and GitHub repositories with automatic backend selection |
| `quick_web_search` | Fast first-pass discovery across web, YouTube, or library docs |
| `deep_research` | Multi-step autonomous research with a cited report |
| `youtube_transcript` | Full transcript for a YouTube video |
| `code_fetch` | Fetch a repository file or tree snapshot from a public code host |
| `composio_similarlinks` | Find pages similar to a given link via Composio |

`code_fetch`, `composio_similarlinks`, and `youtube_transcript` are registered but hidden from MCP client tool listings by default (see `tools/profiles.py`); the CLI calls them directly.

The server also exposes read-only MCP resources (`status://`, `docs://workflow`, `settings://public`, `analytics://`, `cache://stats`) and prompts (`research_methodology`, `query_refinement`, `web_search_workflow`).

### Tool Profiles

Control which tools are exposed via `TOOL_PROFILE`:

| Profile | Tools | Use Case |
|---------|-------|----------|
| `regular` | Every catalog tool except `grok_search` | General AI assistants |
| `full` | All catalog tools | Power users |

```bash
export TOOL_PROFILE="full"
```

## Architecture

```
User Query
    │
    ▼
┌─────────────────┐
│ Query Understanding│──▶ Intent Classification (9 intents)
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│ Provider Selection│──▶ Profile-driven provider weights
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│ Multi-Provider  │──▶ SearXNG, Brave, Tavily, Jina, DDG, ...
│ Search Fanout   │
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│ RRF Merge       │──▶ Reciprocal Rank Fusion (k=60)
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│ Rerank          │──▶ Cross-encoder or GPT-OSS listwise reranker
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│ Result Memory   │──▶ Qdrant vector store for repeat queries
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│ Response        │──▶ Serialized WebSearchResponse
└─────────────────┘
```

### Key Components

- **Search Pipeline** (`search/`) — planning, fanout, RRF merge (`merge.py`), rerank handoff (`ranking.py`), outcome persistence (`outcomes.py`)
- **Content Resolvers** (`content/resolvers/`) — per-host extractors (GitHub, StackExchange, Wikipedia, arXiv, …) assembled in `content/resolver_registry.py`
- **Rerank Engine** (`rerank/`) — multi-engine reranking with bypass policy
- **Entity Extraction** (`ml/gliner_client.py`) — GLiNER2-based entity extraction for query understanding
- **Remote Web Index** (`index/`) — Qdrant HF Space index plus local BM25 encoder
- **Query Understanding** (`search/understanding/`) — LLM-backed intent classification and query rewrite
- **Analytics** (`analytics/`) — DuckDB writers, views, judges, and the dashboard app

## Configuration

### Environment Variables

#### Search Providers

```bash
# Required (at least one)
SEARXNG_BASE_URL="http://localhost:8080"   # SearXNG instance
DEGOOG_BASE_URL="http://localhost:4444"   # DeGoog search aggregator (type="web" auto-selects all engines)
DEGOOG_TIMEOUT_SECONDS="15"              # DeGoog request timeout (default: 15)
TAVILY_API_KEY="tvly-..."                   # Tavily API key
BRAVE_API_KEY="BSA..."                      # Brave Search API key
JINA_API_KEY="jina_..."                     # Jina API key

# Optional providers
GROK_API_KEY="..."                         # xAI/Grok
PARALLEL_API_KEY="pk_..."                    # Parallel AI Search (quick_web_search)


# Provider controls
PROVIDERS_ENABLED="true"                   # Master switch for all providers
DISABLED_PROVIDERS="reddit"                # Comma-separated provider denylist
```

#### Content Extraction

```bash
GITHUB_TOKEN="..."                         # Better GitHub Issue/Discussion extraction
```

#### Query Understanding

```bash
AI_GATEWAY_API_KEY="..."                   # For LLM-backed query rewrite
QUERY_UNDERSTANDING_MODEL="amazon/nova-micro"
GROQ_REWRITE_MODEL="groq/gpt-oss-120b"
```

#### Feature Flags

```bash
TOOL_PROFILE="regular"              # Tool visibility profile
RERANKING_ENABLED="true"           # Enable/disable reranking
QUERY_DECOMPOSITION_ENABLED="true" # Enable query decomposition
QDRANT_SEARCH_ENABLED="true"       # Enable Qdrant result memory
```

#### Rerank Stack

```bash
RERANK_STACK_MODE="bi_cross_llm"          # bi_cross | bi_llm | bi_cross_llm
RERANK_PROVIDER="voyage"                  # voyage | cohere_fast | jina | gcp_cloudrun | local_baseline | none
RERANK_BI_ENCODER_MIN_CANDIDATES="0"      # Extra absolute gate; top_k overfetch guard still applies
RERANK_BI_ENCODER_TIMEOUT_SECONDS="15.0"  # Single-attempt timeout for candidate embedding shortlist calls
RERANK_BI_ENCODER_TEXT_MAX_CHARS="384"    # Bounded title/snippet text sent for candidate embeddings
RERANK_BI_ENCODER_BATCH_SIZE="64"         # Normal rerank windows fit in one bounded embedding request
RERANK_LLM_CANDIDATE_LIMIT="12"           # Max candidates sent to the GPT-OSS worker ladder
RERANK_LLM_TIMEOUT_SECONDS="60.0"         # Timeout for the LLM reranker stage
VOYAGE_API_KEY="..."                       # Voyage reranker auth
JINA_API_KEY="..."                         # Jina reranker auth
COHERE_API_KEY="..."                       # Cohere reranker auth
COHERE_RERANK_MODEL="rerank-v4.0-fast"     # Low-latency Cohere rerank model
COHERE_RERANK_BASE_URL="https://api.cohere.com/v2/rerank"
COHERE_RERANK_TIMEOUT="30.0"
```

`RERANK_STACK_MODE="bi_llm"` uses the existing GPT-OSS 120B worker ladder that is already configured for query understanding/rewrite routing. `bi_cross_llm` runs the bi-encoder shortlist, cross-encoder, GPT-OSS reranker, and diversity stages in the normal path when candidate overfetch justifies reranking. The HF bi-encoder uses bounded title/snippet text and a single normal-window batch; the singleton `AsyncInferenceClient` is reused for connection pooling while per-caller wrappers (Qdrant embedder, bi-encoder batch semaphore) throttle their own traffic.

#### Observability

```bash
PHOENIX_COLLECTOR_ENDPOINT="https://chmielvu-phoenix-observability.hf.space/v1/traces"
```

See `src/kindly_web_search_mcp_server/settings.py` for all 100+ configuration options.

## Development

### Lint/format

```bash
uv run ruff check src/
uv run ruff format --check src/
```

### Run MCP server locally

```bash
uv run mcp-server --http --port 8000
```

### Tests

The `tests/` suite is currently frozen and removed from the repository by project policy — do not add, run, or modify tests. `AGENTS.md` documents the checks that replace it (lint, import smoke, CLI smoke).

## License

MIT
