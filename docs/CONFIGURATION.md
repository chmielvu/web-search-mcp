<!-- generated-by: gsd-doc-writer -->
# Configuration

This document covers all environment variables and configuration options for the Kindly Web Search MCP Server.

## Required Settings

At least one search provider must be configured for the server to function. The server checks for these at startup and will refuse to start if none are set.

### Search Providers

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SEARXNG_BASE_URL` | Recommended | - | Primary search provider (self-hosted, unlimited queries). Example: `http://localhost:8080` |
| `TAVILY_API_KEY` | Optional | - | Tavily search API key (paid provider) |
| `BRAVE_API_KEY` | Optional | - | Brave Search API key (paid provider) |
| `JINA_API_KEY` | Optional | - | Jina AI search API key (conditional provider; also used for reranking) |
| `COMPOSIO_API_KEY` | Optional | - | Composio API key used by Composio LLM Search, Composio Similarlinks, and Composio Image Search |
| `KINDLY_GEMINI_API_KEY` | Optional | - | Gemini API key used both by the standalone `gemini_search` tool and by the Gemini provider inside `web_search` |

**Note:** SearXNG is the recommended primary provider because it is self-hosted and has no query limits. The standard `web_search` mix is SearXNG + DDG + Gemini when configured. Tavily and Brave are disabled by default, while Jina and Composio LLM Search remain conditional.

### Optional API Keys (Recommended)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GITHUB_TOKEN` | Recommended | - | GitHub personal access token for Issues/Discussions extraction. Avoids rate limits. |
| `STACKEXCHANGE_KEY` | Optional | - | StackExchange API key for higher quota when fetching Q&A threads |

---

## SearXNG Configuration

Fine-tune SearXNG requests with these optional settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARXNG_LANGUAGE` | - | Language code for search results (e.g., `en-US`) |
| `SEARXNG_CATEGORIES` | - | Search categories (e.g., `general`) |
| `SEARXNG_ENGINES` | - | Specific engines to use (e.g., `google,bing`) |
| `SEARXNG_TIME_RANGE` | - | Time filter: `day`, `week`, `month`, `year` |
| `SEARXNG_SAFESEARCH` | - | SafeSearch level: `0` (off), `1` (moderate), `2` (strict) |
| `SEARXNG_USER_AGENT` | Chrome UA | Custom User-Agent header |
| `SEARXNG_HEADERS_JSON` | - | JSON object with extra headers (e.g., `{"Authorization":"Bearer ..."}`) |
| `SEARXNG_TIMEOUT_SECONDS` | - | Request timeout override |

---

## Provider Modes

Control when optional providers fire in `web_search`:

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_DDG_MODE` | `always` | DuckDuckGo provider mode (free, always-on) |
| `KINDLY_TAVILY_MODE` | `never` | Tavily provider mode (disabled by default) |
| `KINDLY_BRAVE_MODE` | `never` | Brave provider mode (disabled by default) |
| `KINDLY_JINA_MODE` | `conditional` | Jina provider mode (only when explicitly requested) |
| `KINDLY_GEMINI_SEARCH_MODE` | `always` | Gemini provider mode for `web_search` |
| `KINDLY_COMPOSIO_LLM_SEARCH_MODE` | `always` | Composio LLM Search provider mode |

**Modes:**
- `always`: Always fires when configured (free providers)
- `conditional`: Only when explicitly requested via `providers` parameter
- `never`: Never fires, even if API key present

---

## Feature Flags

Control advanced features with these boolean flags (set to `true` or `false`):

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_SEMANTIC_CACHE_ENABLED` | `true` | Enable LanceDB-backed semantic similarity cache for queries |
| `KINDLY_QUERY_REWRITE_ENABLED` | `true` | Enable query expansion and variant generation |
| `KINDLY_RERANKING_ENABLED` | `true` | Enable Jina cross-encoder reranking for search results |

---

## Semantic Cache

LanceDB-backed fuzzy query matching:

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_LANCEDB_DIR` | `./lancedb_data` | Directory for LanceDB semantic cache storage |
| `KINDLY_SEMANTIC_CACHE_MIN_SCORE` | `0.92` | Minimum similarity score (0.0-1.0) for cache hit |
| `KINDLY_HF_EMBEDDING_MODEL` | `ibm-granite/granite-embedding-97m-multilingual-r2` | Embedding model used for semantic cache vectors |
| `KINDLY_EMBEDDING_DIM` | `384` | Expected embedding vector dimension for the active model |

The semantic cache table name is derived from the active embedding model and dimension. Changing `KINDLY_HF_EMBEDDING_MODEL` or `KINDLY_EMBEDDING_DIM` creates or opens a different LanceDB table, which prevents vector-dimension collisions with older cache data.

---

## Embeddings (HuggingFace Inference Provider)

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_HF_INFERENCE_PROVIDER` | `hf-inference` | HF Inference Provider endpoint |
| `KINDLY_HF_TOKEN` | - | HuggingFace API token (alternative env vars: `HF_TOKEN`, `HUGGINGFACEHUB_API_TOKEN`) |

---

## Query Rewrite (Multi-Provider)

Query expansion and variant generation via multiple LLM providers for free-tier load distribution:

| Variable | Default | Description |
|----------|---------|-------------|
| `MISTRAL_API_KEY` | - | Mistral API key (primary query rewrite provider) |
| `CEREBRAS_API_KEY` | - | Cerebras API key (fallback query rewrite provider) |
| `GROQ_API_KEY` | - | Groq API key (fallback query rewrite provider) |
| `KINDLY_QUERY_REWRITE_MODEL` | `mistral-small-2603` | Primary model for query rewrite |
| `KINDLY_QUERY_REWRITE_TEMPERATURE` | `0.0` | Temperature for rewrite generation (deterministic output) |
| `KINDLY_QUERY_REWRITE_TIMEOUT_SECONDS` | `20` | Timeout for rewrite API calls |
| `KINDLY_QUERY_REWRITE_MAX_VARIANTS` | `3` | Maximum query variants to generate |

### RPM Limits (Free-Tier Load Distribution)

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_QUERY_REWRITE_MISTRAL_RPM` | `30` | Mistral requests-per-minute estimate |
| `KINDLY_QUERY_REWRITE_CEREBRAS_RPM` | `30` | Cerebras requests-per-minute estimate |
| `KINDLY_QUERY_REWRITE_GROQ_RPM` | `30` | Groq requests-per-minute estimate |

---

## Reranking (Jina)

Jina API-based cross-encoder reranking:

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_JINA_RERANK_MODEL` | `jina-reranker-v3` | Jina reranker model |
| `KINDLY_BI_ENCODER_TOP_K` | `100` | Top-K results before cross-encoder reranking |
| `KINDLY_RERANK_TOP_K` | `10` | Final top-K results after reranking |
| `KINDLY_DIVERSITY_THRESHOLD` | `0.85` | Similarity threshold for diversity filtering |
| `KINDLY_MMR_LAMBDA` | `0.5` | MMR lambda parameter (0.0-1.0) for relevance/diversity balance |

---

## RRF Tuning

Reciprocal Rank Fusion merge configuration:

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_RRF_K` | `60` | RRF constant k (Bruch et al. 2022) |
| `KINDLY_RRF_PROVIDER_WEIGHTS` | JSON (see below) | Per-provider weight overrides as JSON object |
| `KINDLY_DEFAULT_NUM_RESULTS` | `5` | Default `num_results` for `web_search` |

**Default Provider Weights:**
```json
{
  "searxng": 1.0,
  "ddg": 0.7,
  "tavily": 1.3,
  "brave": 1.0,
  "jina": 1.1,
  "gemini": 1.2,
  "composio_llm_search": 1.15
}
```

Provider weights rationale (Bruch et al. 2022: per-list weighting is more impactful than k tuning):
- `tavily`: 1.3 (optimized for AI assistants, structured extraction, freshness)
- `gemini`: 1.2 (Google grounding, high recall for factual/research queries)
- `composio_llm_search`: 1.15 (LLM-enhanced relevance ranking)
- `jina`: 1.1 (semantic search expertise)
- `searxng`: 1.0 (baseline, free/open-source aggregator)
- `brave`: 1.0 (baseline, independent index)
- `ddg`: 0.7 (aggregator, penalized for instant answers)

---

## Gemini Grounding

Gemini is used in two separate ways:
- `gemini_search` is the standalone grounded answer tool.
- The Gemini provider inside `web_search` contributes lightweight grounded search hits to the shared merge/rerank pipeline.

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_GEMINI_API_KEY` | - | Gemini API key shared by `gemini_search` and the Gemini `web_search` provider |
| `KINDLY_GEMINI_SEARCH_MODE` | `always` | Provider mode for Gemini inside `web_search`: `always`, `conditional`, or `never` |

**Note:** Model selection uses a hardcoded fallback tier (no env var override):
- Primary: `gemini-2.5-flash`
- Fast fallback: `gemini-2.5-flash-lite`
- Cost fallback: `gemma-4-31b-it`

---

## Perplexity Search (via Pollinations)

AI-synthesized answers with citations using Perplexity Sonar:

| Variable | Default | Description |
|----------|---------|-------------|
| `POLLINATIONS_API_KEY` | - | Pollinations API key for `perplexity_search` tool |
| `POLLINATIONS_BASE_URL` | - | Custom Pollinations API base URL (optional) |

---

## Composio Search Toolkit

Composio is used in three separate ways:
- `composio_llm_search` is a provider inside `web_search`.
- `composio_similarlinks` is a standalone URL-to-related-URLs tool.
- `composio_image_search` is a standalone image metadata search tool.

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPOSIO_API_KEY` | - | Composio API key for direct tool execution |
| `KINDLY_COMPOSIO_USER_ID` | - | Stable Composio user id used for tool execution |
| `KINDLY_COMPOSIO_SEARCH_TOOLKIT_VERSION` | `20260424_00` | Pinned Composio Search toolkit version for parsed outputs |
| `KINDLY_COMPOSIO_LLM_SEARCH_MODE` | `always` | Provider mode for Composio LLM Search inside `web_search` |
| `KINDLY_COMPOSIO_TIMEOUT_SECONDS` | `25` | Timeout for Composio tool execution |
| `KINDLY_COMPOSIO_MAX_RETRIES` | `2` | Composio SDK retry count |

Recommended rollout:

```powershell
$env:COMPOSIO_API_KEY="..."
$env:KINDLY_COMPOSIO_USER_ID="default"
```

---

## YouTube Integration

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL` | - | Proxy URL for YouTube transcript fetching |
| `KINDLY_YOUTUBE_TRANSCRIPT_MAX_CHARS` | `50000` | Maximum characters in transcript output |
| `KINDLY_YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS` | `30` | Timeout for transcript fetch |
| `KINDLY_YOUTUBE_SEARCH_ENGINE` | `youtube` | SearXNG engine for YouTube search |

---

## Browser Automation (nodriver)

Required for universal HTML extraction on JavaScript-heavy sites:

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_BROWSER_EXECUTABLE_PATH` | Auto-detected | Path to Chrome/Chromium/Edge executable. Set explicitly if nodriver cannot auto-detect. |

### Browser Pool

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_NODRIVER_REUSE_BROWSER` | `1` | Enable pooled browser reuse (recommended) |
| `KINDLY_NODRIVER_BROWSER_POOL_SIZE` | `1` | Number of pooled browser instances |
| `KINDLY_NODRIVER_ACQUIRE_TIMEOUT_SECONDS` | `30` | Timeout to acquire browser from pool |
| `KINDLY_NODRIVER_PORT_RANGE` | - | Remote debugging port range (e.g., `45000-45100`) |

### Browser Timeout & Retry

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_HTML_TOTAL_TIMEOUT_SECONDS` | `60` | Total timeout for HTML extraction (max: 600) |
| `KINDLY_NODRIVER_RETRY_ATTEMPTS` | `3` | Startup retry attempts (helps with cold starts) |
| `KINDLY_NODRIVER_RETRY_BACKOFF_SECONDS` | `0.5` | Backoff between retries |
| `KINDLY_NODRIVER_SNAP_BACKOFF_MULTIPLIER` | `3.0` | Extra backoff for Snap-packaged Chromium |
| `KINDLY_NODRIVER_DEVTOOLS_READY_TIMEOUT_SECONDS` | `12` | Timeout for DevTools protocol ready signal |
| `KINDLY_NODRIVER_SANDBOX` | `0` | Chrome sandbox (disabled by default for WSL/Docker reliability) |
| `KINDLY_NODRIVER_ENSURE_NO_PROXY_LOCALHOST` | `1` | Ensure localhost bypasses proxy settings |

---

## Tool Time Budgets

Overall timeout limits for MCP tool execution:

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS` | `120` | Default timeout for tool execution |
| `KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS` | `600` | Maximum allowed timeout (cap) |
| `KINDLY_WEB_SEARCH_MAX_CONCURRENCY` | `3` | Max concurrent provider requests (clamped 1-5) |

---

## Rate Limiting

Differentiated middleware limits are applied per tool group:
- Cheap tools: `web_search`, `get_content`, `gemini_search`
- Expensive tool: `perplexity_search`

**Note:** Environment variable names use `WEB_SEARCH` prefix for backward compatibility, but internally these settings apply to all cheap tools.

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_RATE_LIMIT_WEB_SEARCH_RPS` | `4.0` | Requests/second budget shared by cheap tools |
| `KINDLY_RATE_LIMIT_WEB_SEARCH_BURST` | `12` | Burst token capacity for the cheap-tool rate limiter |
| `KINDLY_RATE_LIMIT_EXPENSIVE_RPS` | `0.5` | Requests/second budget for `perplexity_search` only |
| `KINDLY_RATE_LIMIT_EXPENSIVE_BURST` | `1` | Burst token capacity for `perplexity_search` |

---

## Content Output Limits

Maximum output sizes for various content resolvers:

| Variable | Default | Description |
|----------|---------|-------------|
| `STACKEXCHANGE_MAX_CHARS` | `20000` | Max characters for StackExchange thread output |
| `GITHUB_MAX_CHARS` | `20000` | Max characters for GitHub Issues/Discussions |
| `GITHUB_MAX_COMMENTS` | `50` | Max comments to fetch per GitHub thread |
| `WIKIPEDIA_MAX_CHARS` | `50000` | Max characters for Wikipedia article output |
| `ARXIV_MAX_CHARS` | `50000` | Max characters for arXiv paper markdown |
| `ARXIV_MAX_PAGES` | `30` | Max PDF pages to render for arXiv |
| `KINDLY_GENERIC_PDF_MAX_PAGES` | `20` | Max pages for generic PDF rendering |

---

## Cache TTL

Time-to-live for various cache layers:

| Variable | Default | Description |
|----------|---------|-------------|
| `KINDLY_QUERY_CACHE_TTL_SECONDS` | `86400` (24h) | TTL for exact query cache |
| `KINDLY_PAGE_CACHE_TTL_SECONDS` | `604800` (7d) | TTL for URL-to-content cache |

---

## Summary Generation

Optional content summarization via Chutes API:

| Variable | Default | Description |
|----------|---------|-------------|
| `CHUTES_API_TOKEN` | - | Chutes API token for summary generation |
| `KINDLY_SUMMARY_MODEL` | `zai-org/GLM-5-Turbo` | Model for content summarization |
| `KINDLY_SUMMARY_MAX_TOKENS` | `1200` | Max tokens for summary output |

---

## Server / Transport

HTTP/SSE transport settings (when not using stdio):

| Variable | Default | Description |
|----------|---------|-------------|
| `FASTMCP_HOST` | `127.0.0.1` | Bind host for HTTP/SSE mode |
| `FASTMCP_PORT` | `8000` | Bind port for HTTP/SSE mode |

---

## Logging & Diagnostics

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `WARNING` | Log level (stderr only; keep stdio clean for MCP) |
| `KINDLY_DIAGNOSTICS` | `0` | Enable verbose diagnostics (set to `1`) |
| `KINDLY_STRUCTURED_LOGGING` | `false` | Enable JSON structured logs (set to `true`, `1`, or `yes`) |

---

## Observability (OpenTelemetry)

Grafana Cloud / OpenTelemetry integration for traces, metrics, and logs:

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_SERVICE_NAME` | `kindly-web-search-mcp` | Service name for telemetry |
| `OTEL_SERVICE_VERSION` | `0.1.8` | Service version for telemetry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | - | OTLP endpoint URL (e.g., Grafana Cloud gateway) |
| `OTEL_EXPORTER_OTLP_HEADERS` | - | Auth headers (e.g., `Authorization=Basic%20<token>`) |
| `KINDLY_PROMETHEUS_PORT` | `0` | Prometheus metrics port (0 = disabled) |
| `DEPLOYMENT_ENV` | `development` | Deployment environment label |
| `HOST_OS_TYPE` | `windows` | Host OS type label |
| `PYTHON_VERSION` | `3.12` | Python runtime version label |

**Grafana Cloud Setup Example:**
```powershell
$env:OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-eu-west-2.grafana.net/otlp"
$env:OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic%20<YOUR_GRAFANA_TOKEN>"
```

---

## API Identification

Polite identification for external APIs (recommended):

| Variable | Default | Description |
|----------|---------|-------------|
| `WIKIPEDIA_USER_AGENT` | `kindly-web-search-mcp-server/0.0.1 (contact: you@example.com)` | User-Agent for Wikipedia API requests |
| `ARXIV_USER_AGENT` | `kindly-web-search-mcp-server/0.0.1 (arXiv retriever)` | User-Agent for arXiv API requests |

---

## Configuration Priority

1. **Environment variables** are read at server startup via `os.environ.get()`
2. **Defaults** are hardcoded in `settings.py` and individual modules
3. **Per-request overrides** are not supported — configuration is global

### Startup Validation

The server validates at startup:
- At least one search provider must be configured (`SEARXNG_BASE_URL`, `KINDLY_GEMINI_API_KEY`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, `JINA_API_KEY`, or `COMPOSIO_API_KEY` + `KINDLY_COMPOSIO_USER_ID`)
- If `KINDLY_SEMANTIC_CACHE_ENABLED=true`, the LanceDB directory must be writable
- If `KINDLY_RERANKING_ENABLED=true`, `JINA_API_KEY` must be configured
- If `KINDLY_BROWSER_EXECUTABLE_PATH` is set, the path must point to a valid browser executable
- `KINDLY_MMR_LAMBDA` must be in range [0.0, 1.0]
- `KINDLY_SEMANTIC_CACHE_MIN_SCORE` must be in range (0.0, 1.0]
- `KINDLY_RRF_K` must be > 0

### Example `.env` File

```bash
# Copy to your runtime environment. Do NOT commit secrets.

# Search Providers (provide at least one)
SEARXNG_BASE_URL=http://localhost:8080
TAVILY_API_KEY=
BRAVE_API_KEY=
JINA_API_KEY=
KINDLY_GEMINI_API_KEY=

# Recommended: GitHub token for better Issue extraction
GITHUB_TOKEN=ghp_xxxx

# Optional: Polite API identification
WIKIPEDIA_USER_AGENT=kindly-web-search-mcp-server/0.1.8 (contact: your@email.com)
ARXIV_USER_AGENT=kindly-web-search-mcp-server/0.1.8 (contact: your@email.com)

# Provider modes (control when optional providers fire)
KINDLY_TAVILY_MODE=never
KINDLY_BRAVE_MODE=never
KINDLY_JINA_MODE=conditional
KINDLY_GEMINI_SEARCH_MODE=always

# Feature flags
KINDLY_SEMANTIC_CACHE_ENABLED=true
KINDLY_QUERY_REWRITE_ENABLED=true
KINDLY_RERANKING_ENABLED=true

# Query rewrite (multi-provider load distribution)
MISTRAL_API_KEY=
CEREBRAS_API_KEY=
GROQ_API_KEY=

# Optional: Browser path if auto-detection fails
KINDLY_BROWSER_EXECUTABLE_PATH=

# Optional: Observability (Grafana Cloud)
OTEL_EXPORTER_OTLP_ENDPOINT=
OTEL_EXPORTER_OTLP_HEADERS=
```

---

## Platform-Specific Notes

### WSL/Docker/Headless

- `KINDLY_NODRIVER_SANDBOX=0` is the default — Chrome sandbox often fails in containers
- For Snap-packaged Chromium on Ubuntu, consider increasing `KINDLY_NODRIVER_SNAP_BACKOFF_MULTIPLIER`

### Windows

- `KINDLY_BROWSER_EXECUTABLE_PATH` auto-detects Chrome/Edge; set explicitly if needed:
  - Chrome: `C:\Program Files\Google\Chrome\Application\chrome.exe`
  - Edge: `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`