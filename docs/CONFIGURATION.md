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

### Provider Modes

Providers have three modes controlled by `KINDLY_*_MODE`:

| Mode | Behavior |
|------|----------|
| `always` | Always fires (free providers like SearXNG, DDG) |
| `conditional` | Only when explicitly requested via `providers` param |
| `never` | Never fires, even if API key present |

```bash
# Example: enable Tavily (paid), keep Brave/SearXNG always
export KINDLY_TAVILY_MODE="conditional"
export KINDLY_BRAVE_MODE="always"
export KINDLY_DDG_MODE="always"
```

### Optional Providers

```bash
# Grok/xAI
export GROK_API_KEY="..."

# Perplexity Sonar via Pollinations
export POLLINATIONS_API_KEY="..."

# Google Custom Search Engine
export KINDLY_GOOGLE_CSE_API_KEY="..."
export KINDLY_GOOGLE_CSE_ENGINE_ID="..."

# OpenRouter (shared by Grok, etc.)
export OPENROUTER_API_KEY="..."
```

### Provider Timeout

```bash
export KINDLY_QUERY_REWRITE_CASCADE_TIMEOUT_SECONDS="20"
export KINDLY_CLASSIFIER_TIMEOUT_SECONDS="10"
```

## Query Understanding

LLM-backed intent classification and query rewrite.

```bash
# Primary model
export KINDLY_QUERY_UNDERSTANDING_MODEL="openai/gpt-oss-20b"

# Rewrite cascade (Cerebras → Groq → HF Inference)
export CEREBRAS_API_KEY="..."
export GROQ_API_KEY="..."
export HF_TOKEN="..."
export AI_GATEWAY_API_KEY="..."

# Specific models
export KINDLY_CEREBRAS_REWRITE_MODEL="cerebras/gpt-oss-120b"
export KINDLY_GROQ_REWRITE_MODEL="groq/gpt-oss-120b"
export KINDLY_VERCEL_REWRITE_MODEL="groq/gpt-oss-20b"

# Decomposition
export KINDLY_QUERY_DECOMPOSITION_ENABLED="true"
export KINDLY_DECOMPOSITION_MAX_BRANCHES="10"
export KINDLY_DECOMPOSITION_MAX_CONCURRENCY="4"

# Training data capture
export KINDLY_QUERY_UNDERSTANDING_JSONL_ENABLED="true"
export KINDLY_QUERY_UNDERSTANDING_JSONL_PATH=".kindly/training/query_understanding.jsonl"
```

## Reranking

```bash
# Enable/disable
export KINDLY_RERANKING_ENABLED="true"

# Engine selection (voyage, jina, gcp_cloudrun, local_baseline, none)
export KINDLY_RERANK_PROVIDER="voyage"

# Voyage API
export VOYAGE_API_KEY="..."
export KINDLY_VOYAGE_RERANK_MODEL="rerank-2.5"

# Jina rerank
export KINDLY_JINA_RERANK_MODEL="jina-reranker-v3"

# GCP Cloud Run custom reranker (TEI/FastAPI)
export KINDLY_RERANK_GCP_CLOUDRUN_URL="..."
export KINDLY_RERANK_GCP_MODEL="BAAI/bge-reranker-v2-m3"
export KINDLY_RERANK_GCP_TIMEOUT="30.0"

# Tuning
export KINDLY_BI_ENCODER_TOP_K="100"
export KINDLY_RERANK_TOP_K="10"
export KINDLY_DIVERSITY_THRESHOLD="0.85"
export KINDLY_MMR_LAMBDA="0.5"
export KINDLY_RERANK_SCORE_THRESHOLD="0.0"

# Recency boost
export RERANK_RECENCY_WEIGHT="0.15"
export RERANK_RECENCY_HALF_LIFE_DAYS="90"
```

## Entity Extraction

GLiNER2-based entity extraction for query understanding.

```bash
# Enable (disabled by default)
export KINDLY_ENTITY_EXTRACTION_ENABLED="false"

# Model
export KINDLY_GLINER_MODEL="fastino/gliner2-base-v1"
export KINDLY_GLINER_THRESHOLD="0.5"

# Entity overlap feature for rerank
export KINDLY_RERANK_ENTITY_OVERLAP_ENABLED="false"
export KINDLY_RERANK_ENTITY_OVERLAP_WEIGHT="0.15"
```

## Caching

### Exact Query Cache

In-memory LRU with TTL. No configuration needed.

### Page Cache

DuckDB-backed URL → content cache.

```bash
export KINDLY_PAGE_CACHE_DUCKDB_PATH=".kindly/cache/page_cache.duckdb"
```

### Result Memory

Qdrant-backed semantic cache for repeat queries.

```bash
export KINDLY_RESULT_MEMORY_ENABLED="true"
export KINDLY_RESULT_MEMORY_PATH=""
export KINDLY_RESULT_MEMORY_CANDIDATE_WEIGHT="0.5"
export KINDLY_RESULT_MEMORY_CANDIDATE_LIMIT="5"
export KINDLY_RESULT_MEMORY_MIN_SIMILARITY="0.65"
```

### Web Results Index

Remote Qdrant index on HF Space.

```bash
export KINDLY_WEB_RESULTS_INDEX_ENABLED="false"
export KINDLY_QDRANT_SPACE_URL="https://chmielvu-web-index.hf.space"
export KINDLY_QDRANT_SEARCH_ENABLED="true"
```

## Content Extraction

```bash
# GitHub (better Issue/Discussion extraction)
export GITHUB_TOKEN="..."

# Browser path (for JS-heavy sites)
export KINDLY_BROWSER_EXECUTABLE_PATH="/path/to/chrome"

# YouTube
export KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL=""
export KINDLY_YOUTUBE_TRANSCRIPT_MAX_CHARS="50000"
export KINDLY_YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS="30"

# Academic sources
export KINDLY_S2_API_KEY="..."
export KINDLY_OPENALEX_EMAIL="..."
export KINDLY_OPENALEX_API_KEY="..."
export CROSSREF_MAILTO="..."
export PUBMED_API_KEY="..."
export CORE_API_KEY="..."

# Academic defaults
export KINDLY_ACADEMIC_DEFAULT_SOURCES="arxiv,semanticscholar"
export KINDLY_ACADEMIC_MAX_RESULTS="10"
```

## Tool Visibility

Control which MCP tools are exposed.

```bash
# Profiles: regular, research, media, full
export KINDLY_TOOL_PROFILE="regular"

# Enable tool search (RegexSearchTransform)
export KINDLY_TOOL_SEARCH_ENABLED="false"
```

## Rate Limiting

```bash
# Cheap tools (web_search, etc.)
export KINDLY_RATE_LIMIT_WEB_SEARCH_RPS="4.0"
export KINDLY_RATE_LIMIT_WEB_SEARCH_BURST="12"

# Expensive tools (perplexity_search, etc.)
export KINDLY_RATE_LIMIT_EXPENSIVE_RPS="0.5"
export KINDLY_RATE_LIMIT_EXPENSIVE_BURST="1"
```

## Observability

### OpenTelemetry

```bash
export KINDLY_OTEL_ENABLED="true"
export KINDLY_OTEL_SAMPLING_RATIO="0.15"
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
export KINDLY_ANALYTICS_ENABLED="true"
export KINDLY_ANALYTICS_DUCKDB_PATH=".kindly/analytics/search_events.duckdb"
```

## Agentic Research

Multi-step research agent using LangChain/LangGraph.

```bash
export KINDLY_AGENTIC_RESEARCH_MODEL="Alibaba-NLP/Tongyi-DeepResearch-30B-A3B"
export NANOGPT_API_KEY="..."
export KINDLY_AGENTIC_RESEARCH_TEMPERATURE="0"
export KINDLY_AGENTIC_RESEARCH_TIMEOUT_SECONDS="180"

# Depth profiles
export KINDLY_AGENTIC_RESEARCH_QUICK_RUN_LIMIT="6"
export KINDLY_AGENTIC_RESEARCH_NORMAL_RUN_LIMIT="10"
export KINDLY_AGENTIC_RESEARCH_DEEP_RUN_LIMIT="16"
```

## Composio

```bash
export COMPOSIO_API_KEY="..."
export KINDLY_COMPOSIO_USER_ID="..."
export KINDLY_COMPOSIO_TIMEOUT_SECONDS="25"
```

## RRF Tuning

```bash
# k parameter for Reciprocal Rank Fusion
export KINDLY_RRF_K="60"

# Provider weights (JSON dict)
export KINDLY_RRF_PROVIDER_WEIGHTS='{"searxng": 1.0, "brave": 1.2}'
```
