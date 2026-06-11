# Kindly Web Search MCP Server

Multi-provider web search MCP server for AI coding assistants (Codex, Cursor, Claude Code, etc.). Aggregates results from 15+ search providers using RRF merge, extracts content from 10+ source types, and provides AI-synthesized answers with citations.

## Installation

```bash
pip install kindly-web-search-mcp-server
```

Or with `uvx`:

```bash
uvx kindly-web-search-mcp-server start-mcp-server
```

### From source

```bash
git clone https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server
cd kindly-web-search-mcp-server
pip install -e ".[dev]"
```

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
```

### 2. Run the MCP server

```bash
# Stdio transport (default, for AI coding assistants)
mcp-server

# HTTP transport (for testing/debugging)
mcp-server --http --port 8000
```

### 3. Add to your MCP client config

```json
{
  "mcpServers": {
    "kindly-web-search": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server", "kindly-web-search-mcp-server", "start-mcp-server"]
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `web_search` | Multi-provider web search with RRF merge and rerank |
| `get_content` | Extract full content from a URL (markdown) |
| `batch_get_content` | Fetch content from multiple URLs concurrently |
| `discover_links` | Discover and categorize links from a page |
| `gemini_search` | AI-synthesized answers via Gemini + Google Search |
| `perplexity_search` | AI-synthesized answers via Perplexity Sonar |
| `grok_search` | AI-synthesized answers via Grok/xAI |
| `academic_search` | Search across academic databases (arXiv, PubMed, Semantic Scholar, OpenAlex, CrossRef) |
| `youtube_search` | Search YouTube videos |
| `youtube_transcript` | Get full transcript from YouTube video |
| `quick_web_search` | Fast search via Composio/Exa (experimental) |
| `composio_similarlinks` | Find similar links via Composio |
| `agentic_web_research` | Multi-step research agent (experimental) |
| `analytics_query` | Query search analytics (DuckDB) |
| `analytics_report` | Generate analytics reports |

### Tool Profiles

Control which tools are exposed via `TOOL_PROFILE`:

| Profile | Tools | Use Case |
|---------|-------|----------|
| `regular` | Core search + content tools | General AI assistants |
| `research` | Regular + academic search | Research tasks |
| `media` | Regular + YouTube tools | Media/content tasks |
| `full` | All tools | Power users |

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
│ Rerank          │──▶ Voyage, Jina, FlashRank, or bypass
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

- **Search Pipeline** (`search/pipeline.py`) - Orchestrates the full search flow
- **Content Resolver** (`content/`) - Extracts content from URLs (GitHub, StackExchange, Wikipedia, arXiv, etc.)
- **Rerank Engine** (`rerank/`) - Multi-engine reranking with bypass policy
- **Entity Extraction** (`entity/`) - GLiNER2-based entity extraction for query understanding
- **Result Memory** (`cache/result_memory.py`) - Qdrant-backed semantic cache
- **Query Understanding** (`search/understanding/`) - LLM-backed intent classification and query rewrite

## Configuration

### Environment Variables

#### Search Providers

```bash
# Required (at least one)
SEARXNG_BASE_URL="http://localhost:8080"   # SearXNG instance
TAVILY_API_KEY="tvly-..."                   # Tavily API key
BRAVE_API_KEY="BSA..."                      # Brave Search API key
JINA_API_KEY="jina_..."                     # Jina API key

# Optional providers
GROK_API_KEY="..."                         # xAI/Grok
POLLINATIONS_API_KEY="..."                 # Perplexity Sonar via Pollinations
```

#### Content Extraction

```bash
GITHUB_TOKEN="..."                         # Better GitHub Issue/Discussion extraction
BROWSER_EXECUTABLE_PATH="/path/to/chrome"  # Browser for JS-heavy sites
```

#### Query Understanding

```bash
AI_GATEWAY_API_KEY="..."                   # For LLM-backed query rewrite
QUERY_UNDERSTANDING_MODEL="amazon/nova-micro"
CEREBRAS_REWRITE_MODEL="cerebras/gpt-oss-120b"
GROQ_REWRITE_MODEL="groq/gpt-oss-120b"
```

#### Feature Flags

```bash
TOOL_PROFILE="regular"              # Tool visibility profile
RERANKING_ENABLED="true"           # Enable/disable reranking
QUERY_DECOMPOSITION_ENABLED="true" # Enable query decomposition
QDRANT_SEARCH_ENABLED="true"       # Enable Qdrant result memory
```

#### Observability

```bash
LANGFUSE_PUBLIC_KEY="..."
LANGFUSE_SECRET_KEY="..."
LANGFUSE_BASE_URL="https://cloud.langfuse.com"
```

See `src/kindly_web_search_mcp_server/settings.py` for all 100+ configuration options.

## Development

### Run tests

```bash
pytest
```

Focused test slice:

```bash
python -m pytest tests/test_server.py tests/test_search_orchestrator.py -v
```

### Lint/format

```bash
ruff check src/
ruff format src/
```

### Run MCP server locally

```bash
uvx --from . kindly-web-search-mcp-server start-mcp-server --http --port 8000
```

## License

MIT
