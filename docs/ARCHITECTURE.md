# Architecture: Kindly Web Search MCP Server

## Overview

Kindly is a multi-provider web search MCP server designed for AI coding assistants. It aggregates results from 15+ search providers, extracts content from 10+ source types, and provides AI-synthesized answers with citations.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         MCP Client                              │
│              (Codex, Cursor, Claude Code, etc.)                 │
└───────────────────────────────┬─────────────────────────────────┘
                                │ MCP Protocol (stdio/HTTP)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastMCP Server                             │
│                    (server.py, 16 tools)                        │
└───────────────────────────────┬─────────────────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│   Search Tools    │ │  Content Tools    │ │   AI Search       │
│  web_search       │ │  get_content      │ │  gemini_search    │
│  academic_search  │ │  batch_get_content│ │  perplexity_search│
│  youtube_search   │ │  discover_links   │ │  grok_search      │
└─────────┬─────────┘ └─────────┬─────────┘ └─────────┬─────────┘
          │                     │                     │
          ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Search Pipeline                            │
│                   (search/pipeline.py)                          │
└───────────────────────────────┬─────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│   Query       │       │   Provider    │       │   Result      │
│ Understanding │       │   Selection   │       │   Merge       │
│               │       │               │       │               │
│ • Intent      │       │ • Profile     │       │ • RRF (k=60)  │
│ • Entities    │       │ • Weights     │       │ • Dedup       │
│ • Rewrite     │       │ • Allow-list  │       │ • Host cap    │
└───────────────┘       └───────────────┘       └───────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Rerank Engine                              │
│                   (rerank/core.py)                              │
│                                                                │
│  Engines: Voyage, Jina, GCP CloudRun, Local FlashRank          │
│  Policy:  Bypass for low count/exact literal/navigational      │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Result Memory                              │
│                 (cache/result_memory.py)                        │
│                                                                │
│  Qdrant vector store for semantic caching of repeat queries    │
└─────────────────────────────────────────────────────────────────┘
```

## Search Pipeline Flow

### 1. Query Understanding (`search/understanding/`)

```python
async def resolve_query_understanding(query, research_goal, intent_hint, session_id):
    """LLM-backed intent classification and entity extraction."""
    # 1. Classify intent (9 intents: general, ai_coding, comparison, etc.)
    # 2. Extract entities (GLiNER2 or LLM fallback)
    # 3. Generate must_keep_terms for query rewrite
    # 4. Determine if decomposition is needed
```

### 2. Profile Resolution (`search/profiles/`)

```python
def resolve_search_profile(intent: str) -> SearchProfile:
    """Map intent to search profile with provider weights."""
    # Profiles: default, research, media, full
    # Each profile has:
    # - provider_weights: dict[str, float]
    # - allowed_providers: list[str]
    # - max_results: int
```

### 3. Provider Execution Plan (`search/provider_plan.py`)

```python
def build_provider_execution_plan(profile, context, public_options):
    """Build execution plan with provider weights and arguments."""
    # For each provider in profile:
    #   - Calculate weight based on intent
    #   - Set provider-specific arguments
    #   - Apply semaphores for rate limiting
```

### 4. Branch Execution (`search/branch_executor.py`)

```python
async def execute_search_branches(branches, max_concurrency=8):
    """Execute search queries across providers concurrently."""
    # - Fan-out queries to providers
    # - Apply semaphores for rate limiting
    # - Collect results with diagnostics
```

### 5. RRF Merge (`search/merge.py`)

```python
def merge_search_results(result_lists, weights, host_cap=3):
    """Reciprocal Rank Fusion merge across providers."""
    # k=60 for RRF formula
    # Dedup by canonicalized URL
    # Host cap to prevent single domain dominance
```

### 6. Rerank (`rerank/core.py`)

```python
async def rerank_results(query, candidates, engine="auto"):
    """Multi-engine reranking with bypass policy."""
    # Engines: voyage, jina, gcp_cloudrun, local_baseline, none
    # Policy: bypass for low count, exact literal, navigational
    # Returns: RerankOutput with results + embedding context
```

### 7. Result Memory (`cache/result_memory.py`)

```python
class ResultMemoryStore:
    """Qdrant-backed semantic cache for repeat queries."""
    # - lookup: find similar past results
    # - store: persist successful results
    # - Inject candidates pre-merge (virtual provider)
```

## Content Resolution Pipeline

```
URL Input
    │
    ▼
┌─────────────────┐
│ URL Detection   │──▶ Specialized handler?
└─────────┬───────┘
          │
    ┌─────┴─────┐
    ▼           ▼
┌────────┐  ┌────────┐
│ Yes    │  │ No     │
└────┬───┘  └────┬───┘
     │           │
     ▼           ▼
┌────────────┐ ┌────────────┐
│Specialized │ │HTTP Extract│
│   APIs     │ │(trafilatura)│
└────┬───────┘ └────┬───────┘
     │              │
     ▼              ▼
┌─────────────────────────┐
│   Content Artifact     │
│  (markdown + metadata) │
└─────────────────────────┘
```

### Specialized Content Handlers

| Source Type | Handler | API |
|-------------|---------|-----|
| GitHub Issues | `github_issues.py` | GitHub GraphQL API |
| GitHub Discussions | `github_discussions.py` | GitHub GraphQL API |
| StackExchange | `stackexchange.py` | StackExchange API |
| Wikipedia | `wikipedia.py` | MediaWiki Action API |
| arXiv | `arxiv.py` | arXiv Atom API + PDF |
| YouTube | `youtube.py` | youtube-transcript-api |
| Generic HTML | `http_extract.py` | trafilatura |

## Caching Layers

### 1. Exact Query Cache (`cache/exact_lru.py`)

- In-memory LRU with TTL
- Deterministic for identical queries
- No API calls needed

### 2. Semantic Cache (`cache/semantic_cache.py`)

- LanceDB-backed vector similarity
- Embedding-based fuzzy match
- Handles query paraphrases

### 3. Page Cache (`cache/page_duckdb.py`)

- URL → content cache
- DuckDB-backed with TTL
- SHA256 URL hashing

### 4. Result Memory (`cache/result_memory.py`)

- Qdrant vector store
- Semantic similarity lookup
- Entity overlap boost
- Age decay factor

## Tool Profiles

Control tool visibility via `TOOL_PROFILE`:

```python
TOOL_PROFILES = {
    "regular": {
        "quick_web_search", "web_search", "get_content",
        "batch_get_content", "discover_links", "gemini_search",
        "perplexity_search"
    },
    "research": regular + {"academic_search"},
    "media": regular + {"youtube_search", "youtube_transcript"},
    "full": all_tools
}
```

## Observability Stack

### Telemetry (`telemetry.py`)

- OpenTelemetry traces for all operations
- Prometheus metrics for search/retry/cache
- Langfuse for LLM call tracing

### Grafana Dashboards (`grafana/`)

- `overview-dashboard.json` - High-level metrics
- `providers-dashboard.json` - Provider health
- `pipeline-dashboard.json` - Search pipeline latency
- `cache-dashboard.json` - Cache hit rates
- `content-dashboard.json` - Content resolution
- `quality-dashboard.json` - Rerank quality

### Analytics (DuckDB)

- `analytics/query.py` - Query analytics
- `analytics/report.py` - Report generation
- `evals/judges.py` - LLM-as-judge evaluation

## Key Design Decisions

### 1. Multi-Provider Redundancy

**Decision**: Query multiple providers and merge with RRF.

**Rationale**: No single provider has complete web coverage. RRF provides robust ranking that doesn't depend on score normalization.

### 2. Profile-Driven Provider Selection

**Decision**: Map query intent to provider profiles.

**Rationale**: Different queries benefit from different providers (e.g., academic queries need Semantic Scholar, code queries need GitHub).

### 3. Rerank Always On

**Decision**: Always rerank results (with bypass policy for edge cases).

**Rationale**: Cross-encoder reranking consistently improves result quality, especially for short web snippets.

### 4. Entity Extraction for Query Understanding

**Decision**: Use GLiNER2 for entity extraction, with LLM fallback.

**Rationale**: Entities help with query rewrite, must-keep terms, and result memory lookup.

### 5. No Security/Scalability Constraints

**Decision**: This is a personal/experimental project.

**Rationale**: Optimized for developer experience and feature velocity over production hardening.

## Package Structure

```
src/kindly_web_search_mcp_server/
├── server.py              # MCP server entry point (16 tools)
├── models.py              # Pydantic response models
├── settings.py            # Environment configuration
├── telemetry.py           # OpenTelemetry + Prometheus
├── search/                # Search pipeline
│   ├── pipeline.py        # Orchestrator
│   ├── merge.py           # RRF merge
│   ├── branch_executor.py # Concurrent provider calls
│   ├── understanding/     # LLM-backed query understanding
│   ├── profiles/          # Intent → profile resolution
│   └── provider_plan.py   # Provider execution planning
├── content/               # Content extraction
│   ├── fetch_pipeline.py  # URL → content artifact
│   ├── github_issues.py   # GitHub GraphQL
│   ├── stackexchange.py   # StackExchange API
│   └── ...
├── rerank/                # Reranking engines
│   ├── core.py            # Engine abstraction
│   └── engines.py         # Voyage, Jina, FlashRank
├── cache/                 # Caching layers
│   ├── exact_lru.py       # In-memory LRU
│   ├── page_duckdb.py     # URL → content
│   └── result_memory.py   # Qdrant semantic cache
├── entity/                # Entity extraction
│   ├── models.py          # EntitySpan, etc.
│   └── gliner_client.py   # GLiNER2 client
├── tools/                 # Tool catalog & profiles
├── cli/                   # Native Typer CLI
└── evals/                 # Evaluation framework
```

## Testing Strategy

- **Unit tests**: Mock providers, test pipeline logic
- **Integration tests**: Live provider calls (requires API keys)
- **Eval harness**: LLM-as-judge evaluation for search quality
- **Grafana dashboards**: Visual monitoring in development

## Future Directions

- **Query decomposition**: Break complex queries into sub-queries
- **Streaming**: SSE for long-running operations
- **Multi-modal**: Image/video search via providers
- **Custom rerankers**: User-provided reranking models
