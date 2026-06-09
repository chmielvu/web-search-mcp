<!-- generated-by: gsd-doc-writer -->
# Architecture Overview

The Kindly Web Search MCP Server is a Model Context Protocol (MCP) server designed for AI coding assistants. It provides multi-provider web search with weighted RRF merge, staged content extraction, exact LRU + DuckDB page + Qdrant result-memory caching (no LanceDB/semantic), LLM query understanding and profile resolution, a Python prompt registry plus package-local worker routing, rerank engine abstraction, FastMCP tool profiles + search transform, and comprehensive OpenTelemetry + DuckDB + Langfuse + Grafana observability + offline mcpevals/LLM-judge evals for LLM-ready information retrieval.

## System Overview

The server exposes a dynamic set of MCP tools (via FastMCP profiles: default/research/media/etc and optional RegexSearchTransform) through the FastMCP framework. It operates as a stateless service with exact LRU query cache, DuckDB page cache, and Qdrant result memory (entity-enriched). The architecture follows a pipeline pattern (post Phase 9/10): request → exact LRU → entity extraction → result memory lookup → rewrite → provider search → RRF merge → rerank policy → rerank engine → result memory store → response. Content extraction is handled separately via staged fallback through specialized loaders. AI search tools (gemini/perplexity) and eval/judge harness run outside the hot path.

```mermaid
flowchart TD
    subgraph Entry
        NativeCLI[Native Typer CLI<br>cli/]
        Server[FastMCP Server<br>server.py]
    end

    subgraph Tools["MCP Tools (9)"]
        WS[web_search]
        GC[get_content]
        BGC[batch_get_content]
        GS[gemini_search]
        PS[perplexity_search]
        YT[youtube_transcript]
        YS[youtube_search]
        SL[composio_similarlinks]
        QWS[quick_web_search]
    end

    subgraph Middleware
        ETP[Expensive Tool<br>Protection]
        DRL[Differentiated Rate Limits<br>4 RPS cheap / 0.5 RPS expensive]
        GA[Gemini Advisory]
        QG[Query Quality<br>Guidance]
        RG[Result Guidance]
    end

    subgraph SearchPipeline
        QP[Query Understanding<br>(LLM structured output)]
        QR[Profiled Rewrite<br>Prompt registry + worker ladder]
        PROVIDERS[Search Providers<br>SearXNG/DDG/Gemini/Tavily/Brave/Jina/Composio]
        RRF[Weighted RRF Merge<br>k=60 + provider weights + result_memory virtual list]
        RERANK_POLICY[Rerank Policy<br>bypass for literal/navigational/low-count]
        RERANK[Rerank Engine Abstraction<br>voyage/jina/gcp/local_minilm/none + entity-overlap feature (measured)]
    end

    subgraph ContentPipeline
        RESOLVER[Content Resolver<br>7-stage fallback]
        SE[StackExchange API]
        GI[GitHub Issues GraphQL]
        GD[GitHub Discussions GraphQL]
        WP[Wikipedia API]
        AX[arXiv Atom + PDF]
        HTTP[HTTP Extract<br>trafilatura]
        UH[Universal HTML<br>nodriver/Chromium]
    end

    subgraph Caching
        EQC[Exact Query Cache<br>in-memory LRU (exact_lru + query_cache)]
        PC[Page Cache<br>DuckDB per-URL (page_duckdb)]
        RM[Result Memory<br>Qdrant local (result_memory) + entity overlap]
    end

    subgraph Observability
        OTEL[OpenTelemetry SDK<br>Traces + Metrics + Logs]
        GRAF[Grafana Cloud Export<br>OTLP HTTP]
        PROM[Prometheus Reader<br>Optional local scraping]
    end

    CLI --> Server
    Server --> Middleware
    Middleware --> Tools

    WS --> EQC
    EQC --> ENTITY[Entity Extraction<br>(LLM understanding + overlap)]
    ENTITY --> RM_LOOKUP[Result Memory Lookup]
    RM_LOOKUP --> QP
    QP --> QR
    QR --> PROVIDERS
    PROVIDERS --> RRF
    RRF --> RERANK_POLICY[ Rerank Policy<br>(bypass/eligibility) ]
    RERANK_POLICY --> RERANK
    RERANK --> RM_STORE[Result Memory Store<br>(survivors + entities)]
    RM_STORE --> WS

    GC --> PC
    PC --> RESOLVER
    RESOLVER --> SE
    RESOLVER --> GI
    RESOLVER --> GD
    RESOLVER --> WP
    RESOLVER --> AX
    RESOLVER --> HTTP
    RESOLVER --> UH
    UH --> PC

    GS --> GEMINI[Gemini API<br>Google Search Grounding]
    PS --> POLL[Pollinations API<br>Perplexity Sonar]
    YT --> YTAPI[YouTube Transcript API]
    YS --> YSE[SearXNG YouTube Engine]
    SL --> COMP[Composio SimilarLinks]
    QWS --> COMPQ[Composio LLM Search]

    Tools --> OTEL
    SearchPipeline --> OTEL
    ContentPipeline --> OTEL
    Caching --> OTEL
    OTEL --> GRAF
    OTEL --> PROM
```

## MCP Tools

The server provides **9 MCP tools** with differentiated purposes:

| Tool | Purpose | Returns | Rate Class |
|------|---------|---------|------------|
| `web_search` | Multi-provider URL discovery | Lightweight results (title, link, snippet) | Cheap (4 RPS) |
| `get_content` | Single URL extraction | LLM-ready Markdown with windowing | Cheap (4 RPS) |
| `batch_get_content` | Multi-URL extraction with budget/cursor | Bounded batch results with continuation | Cheap (4 RPS) |
| `gemini_search` | AI-synthesized grounded answer | Answer with [N] citations | Cheap (4 RPS) |
| `perplexity_search` | Deep reasoning synthesis | AI answer with sources | Expensive (0.5 RPS) |
| `youtube_transcript` | Video transcript extraction | Transcript text + metadata | Cheap (4 RPS) |
| `youtube_search` | YouTube video discovery | Video results via SearXNG | Cheap (4 RPS) |
| `composio_similarlinks` | Find related URLs from known URL | Similar links with scores | Cheap (4 RPS) |
| `quick_web_search` | Composio/Exa synthesized answer | Answer with citations | Cheap (4 RPS) |

### Tool Separation Philosophy

The tool contracts follow intentional separation:

- **Search discovers** → `web_search` returns lightweight results
- **Fetch extracts** → `get_content`/`batch_get_content` return LLM-ready Markdown
- **AI search synthesizes** → `gemini_search`/`perplexity_search`/`quick_web_search` return grounded answers

This separation prevents context bloat and allows selective fetching of relevant sources.

### web_search Parameters

```python
@mcp.tool(annotations=ToolAnnotations(
    title="Web Search",
    readOnlyHint=True,
    idempotentHint=True,
    openWorldHint=True,
))
async def web_search(
    query: str,
    research_goal: str,          # REQUIRED: describes search intent
    num_results: int = 5,        # Range 1-10, max capped
    rewrite: bool = True,        # Enable query rewriting
    providers: list[str] | None = None,  # Override provider selection
) -> dict:
```

### get_content Parameters

```python
@mcp.tool(...)
async def get_content(
    url: str,
    char_offset: int = 0,        # Pagination offset
    char_length: int = 20_000,   # Window size (max 50K)
    summary_mode: str = "none",  # "none", "brief", "detailed"
    focus_query: str | None = None,  # Summary focus
) -> dict:
```

Response fields use explicit fetch vocabulary: `input_url`, `normalized_url`, `fetched_url`, `source_type`, `fetch_backend`, `window` (with `has_more`, `next_offset`).

### batch_get_content Parameters

```python
@mcp.tool(...)
async def batch_get_content(
    urls: list[str],
    max_concurrency: int = 4,    # Parallel fetch limit (max 8)
    per_item_char_length: int = 8_000,
    total_char_budget: int = 120_000,  # Max chars returned
    cursor: str | None = None,   # Continuation from prior response
) -> dict:
```

Returns `has_more` and `cursor` for pagination across multiple calls.

## Search Pipeline

### Flow Diagram

```
1. Query received → Normalize query (lowercase, trim)
2. Exact Query Cache lookup (L1, deterministic SHA256 key)
   ├─ Hit: Return cached results
   └─ Miss: Continue
3. Semantic Cache lookup (L2, embedding similarity >= 0.92)
   ├─ Hit: Return cached results
   └─ Miss: Continue
4. Query understanding (LLM structured output on Vercel `amazon/nova-micro`)
   ├─ Intent: `general`, `ai_coding`, `digital_humanities`, `comparison`
   └─ Emits entities + must-keep terms + decomposition signal
5. Profiled rewrite (Python prompt registry + worker ladder)
   ├─ Worker prompts: Cerebras `gpt-oss-120b` → Groq `gpt-oss-120b` → Vercel `gpt-oss-20b`
   └─ Prompt family / temperature / provider args resolved from profile
6. Multi-provider search (tiered, circuit-breaker protected)
   ├─ Tier 1: SearXNG + DDG + Brave + Google CSE + Gemini + Composio (ALWAYS mode where configured)
   ├─ Tier 2: Tavily, Jina, Grok, community providers (mode/request-dependent)
   └─ Per-provider circuit breaker + budget tracking + profile provider arguments
7. Weighted RRF Merge (k=60)
   └─ Provider weights and provider allow-lists resolved from profile
   └─ Host-cap deduplication (max 2 per host in top-k)
8. Reranking pipeline (optional, when candidates > top_k)
   ├─ Bi-encoder filtering (HF Inference embeddings)
   ├─ Voyage reranker 2.5 (primary cross-encoder)
   ├─ Jina reranker v3 (fallback cross-encoder)
   └─ MMR diversity pruning (threshold 0.85)
9. Result memory store (survivors after rerank + entity spans)
10. Return lightweight results (title, link, snippet, provider_count, optional entities)
```

### Provider Modes

| Mode | Behavior | Examples |
|------|----------|----------|
| `ALWAYS` | Fires on every search when configured | SearXNG, DDG, Brave, Google CSE, Gemini, Composio |
| `CONDITIONAL` | Only when caller requests via `providers` param | Jina, Grok, HackerNews, Reddit, GitHub GraphQL, StackExchange |
| `NEVER` | Disabled even if API key present | Tavily |

Configured via the `KINDLY_*_MODE` environment variables in `settings.py`.

### Circuit Breaker

```python
@dataclass
class CircuitBreaker:
    failure_threshold: int = 3        # Open after N consecutive failures
    reset_timeout_seconds: float = 60.0  # Auto-reset after timeout
```

- Per-provider circuit breaker tracks failures
- Opens after 3 consecutive failures
- Auto-resets after 60 seconds (half-open state)
- Telemetry: `record_circuit_breaker_state`, `record_circuit_breaker_event`

### Provider Budget

```python
@dataclass
class ProviderBudget:
    max_calls_per_query: int = 3      # Limit calls per provider per query
    auto_demotion_threshold: float = 0.5  # >50% failure rate = demotion
```

- Tracks per-provider call counts
- Auto-demotes providers with >50% failure rate after 2+ calls
- Resets per query

### Weighted Reciprocal Rank Fusion (RRF)

```python
def merge_search_results(
    result_lists: list[list[WebSearchResult]],
    *,
    k: int = 60,  # RRF constant
    provider_weights: dict[str, float] = {
        "searxng": 1.0,
        "ddg": 0.7,
        "tavily": 1.3,   # Optimized for AI assistants
        "brave": 1.0,
        "jina": 1.1,     # Semantic search expertise
        "gemini": 1.2,   # Google grounding
        "composio_llm_search": 1.15,  # LLM-enhanced ranking
    },
    max_per_host: int = 2,  # Host diversity cap
) -> list[WebSearchResult]:
```

Formula: `score += w_provider × 1/(k + rank)`

- Deduplicates by canonical URL
- Host-cap prevents domain clustering (max 2 per host in top-k)
- Preserves provider attribution (`providers`, `provider_count` fields)

## Content Resolution Pipeline

### 7-Stage Staged Fallback

| Stage | Handler | Source Type | Extraction Method |
|-------|---------|-------------|-------------------|
| 1 | StackExchange API | StackOverflow, SE network | API (full thread) |
| 2 | GitHub Issues GraphQL | GitHub issues | GraphQL |
| 3 | GitHub Discussions GraphQL | GitHub discussions | GraphQL |
| 4 | Wikipedia API | Wikipedia articles | MediaWiki Action API |
| 5 | arXiv Atom API | Academic papers | Atom + PDF → Markdown |
| 6 | HTTP extraction | Static HTML | trafilatura (no browser) |
| 7 | Universal HTML | Dynamic/JS sites | nodriver/Chromium |

### URL Pattern Matching

```python
# StackExchange: *.stackexchange.com, stackoverflow.com, superuser.com
_ISSUE_RE = re.compile(r"/(?:questions|q)/(\d+)(?:/|$)")

# GitHub Issues: github.com/<owner>/<repo>/issues/<number>
_ISSUE_RE = re.compile(r"^/([^/]+)/([^/]+)/issues/(\d+)(?:/|$)")

# GitHub Discussions: github.com/<owner>/<repo>/discussions/<number>
_DISCUSSION_RE = re.compile(r"^/([^/]+)/([^/]+)/discussions/(\d+)(?:/|$)")

# Wikipedia: *.wikipedia.org/wiki/<title>
_WIKI_RE = re.compile(r"^/wiki/([^/]+)$")

# arXiv: arxiv.org/abs/<id> or arxiv.org/pdf/<id>
_ARXIV_RE = re.compile(r"^(?:/abs|/pdf)/(\d+\.\d+|[\w\-]+/\d+)(?:\.pdf)?$")
```

### Fallback Logic

- Stages 1-5: Return error note on failure (API-specific)
- Stages 2-4: Fallback to browser on GraphQL failure (token missing, rate-limit)
- Stage 6: Requires >= 50 words for success
- Stage 7: Browser fallback for JS-heavy sites, skips PDFs

## Caching Strategy

### Three-Tier Architecture (post semantic removal)

```
┌─────────────────────────────────────────────────────────────┐
│                    L1: Exact Query Cache (LRU)               │
│  ─────────────────────────────────────────────────────────── │
│  • In-memory OrderedDict LRU (cache/exact_lru.py)            │
│  • Key: sha256(query|num|rewrite|mode|providers)             │
│  • TTL + max_entries eviction                                │
│  • Fastest lookup: deterministic, no disk                    │
└─────────────────────────────────────────────────────────────┘
                           ↓ Miss
┌─────────────────────────────────────────────────────────────┐
│              L2: Result Memory (Qdrant + entities)           │
│  ─────────────────────────────────────────────────────────── │
│  • Qdrant local (:memory: or .kindly/result_memory)          │
│  • Historical (query, results) vectors + entity spans        │
│  • Injected as low-weight virtual provider list into RRF     │
│  • Survival tracked via result_memory.candidate_survived     │
└─────────────────────────────────────────────────────────────┘
                           ↓ Miss (for content)
┌─────────────────────────────────────────────────────────────┐
│                    L3: Page Cache (DuckDB)                   │
│  ─────────────────────────────────────────────────────────── │
│  • Separate DuckDB file (.kindly/cache/page_cache.duckdb)    │
│  • URL-hash key, metadata JSON, TTL                          │
│  • Locked writes; no contention with analytics DuckDB        │
└─────────────────────────────────────────────────────────────┘
```

Result memory candidates participate in merge/rerank and can survive to final results (observable).

### No more LanceDB / semantic cache (removed Phase 5.3)
All LanceDB/semantic symbols, settings (KINDLY_LANCEDB_DIR, KINDLY_SEMANTIC_CACHE_*), and modules (semantic_cache.py, store.py, schema.py) are deleted from runtime. Page cache moved to dedicated DuckDB.

### SingleFlight Pattern

Request coalescing for concurrent identical searches:

```python
_search_flight = SingleFlight()
response = await _search_flight.do(flight_key, _execute_search)
```

Multiple concurrent requests for the same query share a single execution.

## Query Rewrite

### Simplified Mode: Bypass vs Expand

```python
RewriteMode = Literal["bypass", "expand"]

class RewritePolicy(BaseModel):
    mode: RewriteMode
    reason: str
    must_keep_terms: list[str]  # Exact literals to preserve
```

**No intent classification** — just precision signal detection.

### Precision Signals → Bypass

```python
_PRECISION_PATTERNS = (
    re.compile(r"https?://"),           # URLs
    re.compile(r'["`][^"`]{4,}["`]'),   # Quoted strings
    re.compile(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b"),  # Repo names
    re.compile(r"\b\d+(?:\.\d+){1,3}\b"),  # Versions (1.2.3)
    re.compile(r"0x[0-9A-Fa-f]{4,8}"),  # Hex codes
    re.compile(r"E[A-Z]+[0-9]+"),       # Error codes (EINVAL)
    re.compile(r"--[A-Za-z0-9_-]+"),    # CLI flags
    re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-..."),  # UUIDs
    re.compile(r"\b[0-9a-fA-F]{7,40}\b"),  # Git hashes
)
```

Search operators (`site:`, `filetype:`, `inurl:`, etc.) also trigger bypass.

### Multi-Provider LLM Router

```python
# Free-tier load distribution across providers
AI_GATEWAY_API_KEY, CEREBRAS_API_KEY, GROQ_API_KEY

# Intent-specific temperatures
TEMPERATURE_BY_INTENT = {
    "code": 0.15,           # Deterministic for technical queries
    "general_research": 0.5, # Balanced creativity
    "comparison": 0.3,       # Structured for entity comparisons
}
```

Provider-aware routing generates keyword-targeted and neural-targeted variants based on active providers.

## Reranking Pipeline

### Three-Stage Pipeline

```python
async def rerank_results(
    query: str,
    candidates: list[WebSearchResult],
    top_k: int = 10,
) -> list[WebSearchResult]:
```

1. **Bi-encoder filtering**: Embedding similarity filter when candidates > top_k × 2
2. **Voyage rerank-2.5**: Primary cross-encoder relevance scoring, with Jina fallback when configured
3. **MMR diversity**: Maximal Marginal Relevance with host diversity

### MMR Diversity Pruning

```python
diversified_rank = maximal_marginal_relevance_rank(
    query_embedding,
    embeddings,
    scoped_urls,
    lambda_param=settings.mmr_lambda_param,  # 0.5 by default
    max_per_host=2,
)
```

## Embeddings

### HF Inference Provider

```python
hf_embedding_model: str = "ibm-granite/granite-embedding-97m-multilingual-r2"
embedding_dim: int = 384
```

Used for:
- Semantic cache similarity scoring
- Bi-encoder reranking filter
- MMR diversity pruning

## Middleware Stack

### Order (per call)

1. **Expensive Tool Protection**: Blocks first `perplexity_search` call, returns steering message
2. **Differentiated Rate Limits**: 4 RPS for cheap tools, 0.5 RPS for expensive
3. **Gemini Advisory**: Non-blocking query quality tips for `gemini_search`
4. **Query Quality Middleware**: Tips on every `web_search` call
5. **Result Guidance Middleware**: Extraction guidance on every `web_search` result

### Rate Limit Configuration

```python
rate_limit_cheap_rps: float = 4.0      # web_search, get_content, gemini_search
rate_limit_cheap_burst: int = 12
rate_limit_expensive_rps: float = 0.5  # perplexity_search
rate_limit_expensive_burst: int = 1
```

## Observability

### OpenTelemetry Integration

Comprehensive telemetry following:
- OTEL HTTP Semantic Conventions
- OTEL MCP Semantic Conventions (emerging standard)
- Grafana Cloud Application Observability best practices

```python
init_telemetry(
    service_name="web-search-mcp",
    service_version="0.1.8",
)
```

Environment variables:
- `OTEL_EXPORTER_OTLP_ENDPOINT`: Grafana Cloud OTLP gateway
- `OTEL_EXPORTER_OTLP_HEADERS`: Authorization header
- `KINDLY_PROMETHEUS_PORT`: Optional Prometheus endpoint for Alloy scraping

### Three-Layer Observability Model

| Layer | Scope | Metrics |
|-------|-------|---------|
| Transport | JSON-RPC health, MCP session | Message latency |
| Tool Execution | Provider calls, cache, RRF, content | Per-stage duration, counts |
| Agentic | Task success, self-correction | Query length, domain diversity |

### Key Metrics

- **Provider**: `web_search_provider_calls_total`, `web_search_provider_duration_seconds`
- **Search**: `web_search_requests_total`, `web_search_duration_seconds`
- **Cache**: `web_search_cache_requests_total`, `web_search_exact_lru_hit_total`, `result_memory.lookup_total` (no semantic)
- **RRF**: `web_search_rrf_merge_total`, `web_search_rrf_score_distribution`
- **Rerank**: `web_search_rerank_total`, `web_search_rerank_scores`
- **Circuit**: `web_search_provider_circuit_state`, `web_search_provider_circuit_events`
- **Query Quality**: `web_search_query_length_chars`, `web_search_domain_diversity`

### HTTPX Auto-Instrumentation

All HTTP client calls automatically traced with OTEL semantic conventions.

## Key Abstractions

### WebSearchResult Model

```python
class WebSearchResult(BaseModel):
    title: str
    link: str                      # Canonical URL
    snippet: str
    domain: str | None
    resource_type: str | None      # web, pdf, youtube, github
    mime_hint: str | None
    providers: list[str] | None    # Providers that surfaced this
    provider_count: int | None     # Agreement signal
    score: float | None            # RRF/reranked score
```

### RewritePolicy Model

```python
class RewritePolicy(BaseModel):
    mode: RewriteMode  # "bypass" | "expand"
    reason: str
    must_keep_terms: list[str]  # Exact literals to preserve
```

### QueryRewritePlan Model

```python
class QueryRewritePlan(BaseModel):
    original_query: str
    policy: RewritePolicy
    variants: list[QueryVariant]
    final_queries: list[str]  # Deduplicated, limited queries
```

## Directory Structure

```
src/kindly_web_search_mcp_server/
├── __init__.py              # Package exports
├── __main__.py              # Entry point for uvx
├── cli/                     # Native Typer web-search-cli package
├── server.py                # FastMCP server registration
├── models.py                # Pydantic response models
├── settings.py              # Environment-first configuration
├── errors.py                # Error classification
├── telemetry.py             # OpenTelemetry instrumentation
│
├── search/                  # Search pipeline
│   ├── __init__.py          # Provider registry, circuit breaker, budget
│   ├── pipeline.py          # Main 0.2 pipeline coordination
│   ├── provider_config.py   # Provider mode configuration
│   ├── provider_plan.py     # Profile-derived provider firing plan
│   ├── provider_options.py  # Provider-specific option bundles
│   ├── provider_call.py     # Provider argument forwarding helper
│   ├── searxng.py           # SearXNG provider
│   ├── ddg.py               # DuckDuckGo provider
│   ├── tavily.py            # Tavily provider
│   ├── brave.py             # Brave provider
│   ├── jina.py              # Jina provider
│   ├── gemini_pollinations.py  # Gemini via Pollinations
│   ├── composio_llm_search.py  # Composio LLM search
│   ├── pollinations.py      # Perplexity Sonar
│   ├── youtube.py           # YouTube search
│   ├── merge.py             # Weighted RRF implementation
│   ├── query_policy.py      # Rewrite policy model
│   ├── pipeline_builders.py # Search context, rewrite variants, prompt wiring
│   ├── understanding/       # LLM query understanding schema/resolver
│   ├── profiles/            # Profile definitions and inheritance
│   ├── prompts/             # Prompt registry and provider prompt builders
│   ├── query_rewrite_models.py  # Query variant model used by the live pipeline
│   ├── gemini_search_tool.py # Gemini grounding MCP tool
│   └── normalize.py         # Query/URL normalization
│
├── content/                 # Content resolution
│   ├── resolver.py          # 7-stage fallback coordinator
│   ├── batch_orchestrator.py # Batch fetch orchestration
│   ├── fetch_pipeline.py    # Single URL fetch pipeline
│   ├── windowing.py         # Content windowing/slicing
│   ├── summary.py           # Optional summarization
│   ├── stackexchange.py     # StackOverflow/SE API
│   ├── github_issues.py     # GitHub Issues GraphQL
│   ├── github_discussions.py # GitHub Discussions GraphQL
│   ├── wikipedia.py         # Wikipedia API
│   ├── arxiv.py             # arXiv API
│   └── youtube.py           # YouTube transcript
│
├── scrape/                  # Scraping utilities
│   ├── universal_html.py    # Nodriver/Chromium loader
│   ├── chromium_pool.py     # Browser instance pooling
│   ├── http_extract.py      # Trafilatura extraction
│   ├── extract.py           # HTML → Markdown
│   ├── sanitize.py          # Markdown cleanup
│   ├── fetch.py             # HTTP fetch helpers
│   └── nodriver_worker.py   # Browser subprocess
│
├── cache/                   # Caching layers (LanceDB/semantic removed)
│   ├── query_cache.py       # Exact LRU wrapper (L1)
│   ├── exact_lru.py         # In-memory OrderedDict LRU impl
│   ├── page_cache.py        # DuckDB page cache wrapper (L3)
│   ├── page_duckdb.py       # Separate DuckDB backend for pages
│   ├── result_memory.py     # Qdrant result memory (L2 historical)
│   ├── content_type.py      # Content classification (for TTLs)
│   └── observability.py     # Cache event emission
│
├── entity/                  # Entity extraction (Phase 6/8)
│   ├── models.py            # EntitySpan
│   ├── default_schema.py    # coding/web labels
│   ├── gliner_client.py     # legacy lazy GLiNER2 client (not part of 0.2 hot path)
│   ├── chunk.py
│   ├── postprocess.py
│   └── overlap.py
│
├── evals/                   # Eval harness + judges (Phase 1/4/9)
│   ├── cases.py metrics.py judges.py runner.py
│   └── (offline only; mcpevals + Langfuse + DuckDB)
│
├── embeddings/              # Embedding services
│   ├── hf_inference.py      # HF Inference Provider client
│   └── rate_limiter.py      # Batch rate limiting
│
├── rerank/                  # Reranking pipeline
│   ├── core.py              # Pipeline orchestration
│   ├── bi_encoder.py        # Bi-encoder filtering
│   ├── voyage.py            # Voyage reranker 2.5
│   ├── jina.py              # Jina fallback reranker
│   └── diversity.py         # MMR diversity pruning
│
├── middleware/              # FastMCP middleware
│   ├── expensive_tool_protection.py  # perplexity blocking
│   ├── rate_limits.py       # Differentiated rate limiting
│   ├── gemini_advisory.py   # gemini_search advisory
│   └── query_guidance.py    # Query quality tips
│
├── composio_tools.py        # Composio tool registration
│
└── utils/                   # Utilities
    ├── diagnostics.py       # Diagnostic logging
    ├── logging.py           # Logging configuration
    ├── observability.py     # Event emission helpers
    ├── singleflight.py      # Request coalescing
    └── structured_logging.py # Structured JSON logging
```

## Environment Configuration

All configuration is environment-first via `settings.py`. See [CONFIGURATION.md](./CONFIGURATION.md) for complete reference.

### Key Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `SEARXNG_BASE_URL` | Primary search provider | Required (or other provider) |
| `AI_GATEWAY_API_KEY` | Query understanding and rewrite workers | Optional |
| `CEREBRAS_API_KEY` | Query rewrite worker tier 1 | Optional |
| `GROQ_API_KEY` | Query rewrite worker tier 2 | Optional |
| `GITHUB_TOKEN` | GitHub GraphQL API | Recommended |
| `KINDLY_GEMINI_API_KEY` | Gemini grounding | Optional |
| `POLLINATIONS_API_KEY` | Perplexity Sonar | Optional |
| `COMPOSIO_API_KEY` + `KINDLY_COMPOSIO_USER_ID` | Composio search | Optional |
| `KINDLY_SEMANTIC_CACHE_ENABLED` | Semantic cache toggle | `true` |
| `KINDLY_SEMANTIC_CACHE_MIN_SCORE` | Similarity threshold | `0.92` |
| `KINDLY_RERANKING_ENABLED` | Reranking toggle | `true` |
| `KINDLY_RERANK_PROVIDER` | Primary reranker provider | `voyage` |
| `KINDLY_VOYAGE_RERANK_MODEL` | Voyage reranker model | `rerank-2.5` |
| `KINDLY_ANALYTICS_ENABLED` | DuckDB analytics event capture | `true` |
| `KINDLY_QUERY_REWRITE_CASCADE_TIMEOUT_SECONDS` | Rewrite worker cascade timeout | `20` |
| `KINDLY_PAGE_CACHE_DUCKDB_PATH` | DuckDB for page cache (separate from analytics) | `.kindly/cache/page_cache.duckdb` |
| `KINDLY_RESULT_MEMORY_PATH` | Qdrant path or :memory: | `.kindly/result_memory` (empty for in-mem) |

<!-- VERIFY: External service URLs like Grafana Cloud OTLP endpoints are configured via environment variables -->
<!-- VERIFY: Rate limit values are defaults from settings.py and can be overridden via KINDLY_RATE_LIMIT_* env vars -->

## Design Patterns

### Staged Fallback Pattern

Content resolution uses staged fallback for resilience:

```python
async def resolve_page_content_markdown(url: str) -> str | None:
    # Stage 1: Try specialized API
    try:
        parse_stackexchange_url(url)
        return await fetch_stackexchange_thread_markdown(url)
    except StackExchangeError:
        pass  # Not a StackExchange URL, continue

    # Stage 2-N: Similar pattern for GitHub, Wikipedia, arXiv...

    # Stage 6: HTTP extraction (no browser)
    result = await http_extract(url, timeout=15.0)
    if result.word_count >= 50:
        return result.text

    # Stage 7: Universal HTML (browser fallback)
    return await load_url_as_markdown(url)
```

### Circuit Breaker Pattern

```python
if _circuit_breaker.is_open(provider_name):
    return []  # Skip unhealthy provider

try:
    results = await provider_fn(query, num_results, http_client)
    _circuit_breaker.record_success(provider_name)
except Exception:
    _circuit_breaker.record_failure(provider_name)
    return []
```

### Middleware Chain Pattern

```python
mcp.add_middleware(create_expensive_tool_middleware())
mcp.add_middleware(create_differentiated_rate_limit_middleware(...))
mcp.add_middleware(create_gemini_advisory_middleware())
mcp.add_middleware(create_query_quality_middleware())
mcp.add_middleware(create_result_guidance_middleware())
```

Blocking middleware raises `ToolError`; advisory middleware logs tips.

## Resources and Prompts

### 3 MCP Resources

| Resource | URI | Purpose |
|----------|-----|---------|
| Provider Status | `status://providers` | Shows configured search providers |
| Feature Status | `status://features` | Shows enabled features and cache settings |
| Workflow Doc | `docs://workflow` | Recommended workflow for tool usage |

### 3 MCP Prompts

| Prompt | Purpose | Template |
|--------|---------|----------|
| `debug_error_prompt` | Debug error using web search | "Debug this error: {error_message}" |
| `research_topic_prompt` | Research a topic | "Research: {topic} (depth: {depth})" |
| `find_library_docs_prompt` | Find library documentation | "Find docs for: {library} - {feature}" |

## Related Documentation

- [CONFIGURATION.md](./CONFIGURATION.md) — Environment variables and settings
- [GETTING-STARTED.md](./GETTING-STARTED.md) — Quick start guide
- [DEVELOPMENT.md](./DEVELOPMENT.md) — Development patterns and workflows
- [TESTING.md](./TESTING.md) — Testing guide and mock patterns
