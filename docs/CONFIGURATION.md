# Configuration Reference

All configuration is via environment variables. Defaults are sensible for local development.

## Search Providers

### Required (at least one)

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARXNG_BASE_URL` | - | SearXNG instance URL (self-hosted, no API key) |
| `TAVILY_API_KEY` | - | Tavily API key |
| `BRAVE_API_KEY` | - | Brave Search API key |
| `JINA_API_KEY` | - | Jina API key |

### Provider Selection

Provider selection is driven by the resolved search plan and explicit caller allow-lists.
Configure the API keys or base URLs for the providers you want available; the registry
will include them when they are configured and selected by the live plan.

### Optional Providers

```bash
# Grok/xAI
export GROK_API_KEY="..."

# Perplexity Sonar via Pollinations
export POLLINATIONS_API_KEY="..."

# Google Custom Search Engine
export GOOGLE_CSE_API_KEY="..."
export GOOGLE_CSE_ENGINE_ID="..."

# OpenRouter (shared by Grok, etc.)
export OPENROUTER_API_KEY="..."
```

### Provider Tuning

Some providers expose small, provider-specific knobs:

```bash
# StackExchange site list used by the Q&A provider
export STACKEXCHANGE_SITES="stackoverflow"

# Reddit courtesy delay between requests
export REDDIT_DELAY_SECONDS="2"
```

### Provider Timeout

```bash
export QUERY_REWRITE_CASCADE_TIMEOUT_SECONDS="20"
export CLASSIFIER_TIMEOUT_SECONDS="10"
```

## Query Understanding

LLM-backed intent classification and query rewrite.

```bash
# Primary model
export QUERY_UNDERSTANDING_MODEL="openai/gpt-oss-20b"

# Rewrite cascade (Cerebras → Groq → HF Inference)
export CEREBRAS_API_KEY="..."
export GROQ_API_KEY="..."
export HF_TOKEN="..."
export AI_GATEWAY_API_KEY="..."

# Specific models
export CEREBRAS_REWRITE_MODEL="cerebras/gpt-oss-120b"
export GROQ_REWRITE_MODEL="groq/gpt-oss-120b"
export VERCEL_REWRITE_MODEL="groq/gpt-oss-20b"

# Decomposition
export QUERY_DECOMPOSITION_ENABLED="true"
export DECOMPOSITION_MAX_BRANCHES="10"
export DECOMPOSITION_MAX_CONCURRENCY="4"

# Training data capture
export QUERY_UNDERSTANDING_JSONL_ENABLED="true"
export QUERY_UNDERSTANDING_JSONL_PATH=".kindly/training/query_understanding.jsonl"
```

## Reranking

```bash
# Enable/disable
export RERANKING_ENABLED="true"

# Engine selection (voyage, jina, gcp_cloudrun, local_baseline, none)
export RERANK_PROVIDER="voyage"

# Voyage API
export VOYAGE_API_KEY="..."
export VOYAGE_RERANK_MODEL="rerank-2.5"

# Jina rerank
export JINA_RERANK_MODEL="jina-reranker-v3"

# GCP Cloud Run custom reranker (TEI/FastAPI)
export RERANK_GCP_CLOUDRUN_URL="..."
export RERANK_GCP_MODEL="BAAI/bge-reranker-v2-m3"
export RERANK_GCP_TIMEOUT="30.0"

# Tuning
export BI_ENCODER_TOP_K="100"
export RERANK_TOP_K="10"
export DIVERSITY_THRESHOLD="0.85"
export MMR_LAMBDA="0.5"
export RERANK_SCORE_THRESHOLD="0.0"

# Recency boost
export RERANK_RECENCY_WEIGHT="0.15"
export RERANK_RECENCY_HALF_LIFE_DAYS="90"
```

## Entity Extraction

GLiNER2-based entity extraction for query understanding.

```bash
# Enable (disabled by default)
export ENTITY_EXTRACTION_ENABLED="false"

# Model
export GLINER_MODEL="fastino/gliner2-base-v1"
export GLINER_THRESHOLD="0.5"

# Entity overlap feature for rerank
export RERANK_ENTITY_OVERLAP_ENABLED="false"
export RERANK_ENTITY_OVERLAP_WEIGHT="0.15"
```

## Caching

### Exact Query Cache

In-memory LRU with TTL. No configuration needed.

### Page Cache

DuckDB-backed URL → content cache.

```bash
export PAGE_CACHE_DUCKDB_PATH=".kindly/cache/page_cache.duckdb"
```

### Result Memory

Qdrant-backed semantic cache for repeat queries.

```bash
export RESULT_MEMORY_ENABLED="true"
export RESULT_MEMORY_PATH=""
export RESULT_MEMORY_CANDIDATE_WEIGHT="0.5"
export RESULT_MEMORY_CANDIDATE_LIMIT="5"
export RESULT_MEMORY_MIN_SIMILARITY="0.65"
```

### Web Results Index

Remote Qdrant index on HF Space.

```bash
export WEB_RESULTS_INDEX_ENABLED="false"
export QDRANT_SPACE_URL="https://chmielvu-web-index.hf.space"
export QDRANT_SEARCH_ENABLED="true"
```

## Content Extraction

```bash
# GitHub (better Issue/Discussion extraction)
export GITHUB_TOKEN="..."

# Browser path (for JS-heavy sites)
export BROWSER_EXECUTABLE_PATH="/path/to/chrome"

# YouTube
export YOUTUBE_TRANSCRIPT_PROXY_URL=""
export YOUTUBE_TRANSCRIPT_MAX_CHARS="50000"
export YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS="30"

# Academic sources
export S2_API_KEY="..."
export OPENALEX_EMAIL="..."
export OPENALEX_API_KEY="..."
export CROSSREF_MAILTO="..."
export PUBMED_API_KEY="..."
export CORE_API_KEY="..."

# Academic defaults
export ACADEMIC_DEFAULT_SOURCES="arxiv,semanticscholar"
export ACADEMIC_MAX_RESULTS="10"
```

## Tool Visibility

Control which MCP tools are exposed.

```bash
# Profiles: regular, research, media, full
export TOOL_PROFILE="regular"

# Enable tool search (RegexSearchTransform)
export TOOL_SEARCH_ENABLED="false"
```

## Rate Limiting

```bash
# Cheap tools (web_search, etc.)
export RATE_LIMIT_WEB_SEARCH_RPS="4.0"
export RATE_LIMIT_WEB_SEARCH_BURST="12"

# Expensive tools (perplexity_search, etc.)
export RATE_LIMIT_EXPENSIVE_RPS="0.5"
export RATE_LIMIT_EXPENSIVE_BURST="1"
```

## Observability

### OpenTelemetry

```bash
export OTEL_ENABLED="true"
export OTEL_SAMPLING_RATIO="0.15"
export OTEL_SERVICE_NAME="web-search-mcp"
export DEPLOYMENT_ENV="development"

# Grafana Cloud
export GRAFANA_CLOUD_INSTANCE_ID="..."
export GRAFANA_CLOUD_API_KEY="..."
export GRAFANA_CLOUD_OTLP_ENDPOINT="..."
```

### Langfuse

```bash
export LANGFUSE_PUBLIC_KEY="..."
export LANGFUSE_SECRET_KEY="..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
```

### Analytics (DuckDB)

```bash
export ANALYTICS_ENABLED="true"
export ANALYTICS_DUCKDB_PATH=".kindly/analytics/search_events.duckdb"
```

## Agentic Research

Multi-step research agent using LangChain/LangGraph.

```bash
export AGENTIC_RESEARCH_MODEL="Alibaba-NLP/Tongyi-DeepResearch-30B-A3B"
export NANOGPT_API_KEY="..."
export AGENTIC_RESEARCH_TEMPERATURE="0"
export AGENTIC_RESEARCH_TIMEOUT_SECONDS="180"

# Depth profiles
export AGENTIC_RESEARCH_QUICK_RUN_LIMIT="6"
export AGENTIC_RESEARCH_NORMAL_RUN_LIMIT="10"
export AGENTIC_RESEARCH_DEEP_RUN_LIMIT="16"
```

## Composio

```bash
export COMPOSIO_API_KEY="..."
export COMPOSIO_USER_ID="..."
export COMPOSIO_TIMEOUT_SECONDS="25"
```

## RRF Tuning

```bash
# k parameter for Reciprocal Rank Fusion
export RRF_K="60"

# Provider weights (JSON dict)
export RRF_PROVIDER_WEIGHTS='{"searxng": 1.0, "brave": 1.2}'
```
