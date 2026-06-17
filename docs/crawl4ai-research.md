# Crawl4AI Research Report

## Date: 2026-06-17

## Repository Overview
- **Repo**: `unclecode/crawl4ai`
- **Description**: Asynchronous web crawling & scraping framework with LLM integration
- **Key source lines**: ~16,871 across 30+ Python modules
- **Docker image**: `unclecode/crawl4ai:latest`
- **API port**: 11235

---

## 1. Extraction Strategies (crawl4ai/extraction_strategy.py, 2,827 lines)

**Base**: `ExtractionStrategy(ABC)` — has `extract(url, html)`, `run(url, sections)`, `arun()`

### LLMExtractionStrategy
```python
class LLMExtractionStrategy(ExtractionStrategy):
    def __init__(self, provider=DEFAULT_PROVIDER, api_token=None, instruction=None,
                 schema=None, extraction_type='block', chunk_token_threshold=4000,
                 overlap_rate=0.1, word_token_rate=1.3, apply_chunking=True,
                 base_url=None, extra_args={}, force_json_response=False)
```
- Key extra: `agenerate_schema(html, query, llm_config, max_attempts=3)`
- Uses chunking + LLM per chunk with merge

### CosineStrategy (called CosineSimilarityStrategy in docs)
```python
class CosineStrategy(ExtractionStrategy):
    def __init__(self, semantic_filter=None, word_count_threshold=10, max_dist=0.2,
                 linkage_method='ward', top_k=3,
                 model_name='sentence-transformers/all-MiniLM-L6-v2', sim_threshold=0.3)
```
- Uses sentence-transformers embeddings + hierarchical clustering + BM25-like filtering

### RegexExtractionStrategy
```python
class RegexExtractionStrategy(ExtractionStrategy):
    def __init__(self, pattern=_B.NOTHING, *, custom=None, input_format='fit_html')
```
- Built-in patterns: EMAIL, URL, PHONE, DATE, TIME, MONEY, IP_ADDRESS, HTML_TAG, etc.
- Class method: `generate_pattern(label, html, query, llm_config)` — LLM-assisted regex generation

### JsonCssExtractionStrategy
```python
class JsonCssExtractionStrategy(JsonElementExtractionStrategy):
    def __init__(self, schema: Dict[str, Any])
```
Schema format: `{"name": "...", "baseSelector": "div.item", "fields": [{...}]}`

### JsonXPathExtractionStrategy
```python
class JsonXPathExtractionStrategy(JsonElementExtractionStrategy):
    def __init__(self, schema: Dict[str, Any])
```
Has `_css_to_xpath()` and `_basic_css_to_xpath()` converters.

---

## 2. Deep Crawl Strategies (crawl4ai/deep_crawling/)

### BFSDeepCrawlStrategy (420 lines)
```python
BFSDeepCrawlStrategy(max_depth, filter_chain=FilterChain(), url_scorer=None,
                     include_external=False, score_threshold=-inf, max_pages=inf,
                     resume_state=None, on_state_change=None, should_cancel=None)
```
Level-by-level via `crawler.arun_many()`. Supports batch + stream + cancellation + state export.

### DFSDeepCrawlStrategy (331 lines)
Extends BFS. Uses stack (`.pop()`) + `_dfs_seen` set. Pushes children in reverse order for correct traversal.

### BestFirstCrawlingStrategy (421 lines)
Uses `asyncio.PriorityQueue`. Items: `(-score, depth, url, parent)`. Batch size = 10. Rediscovered links are scored and re-queued.

### FilterChain (filters.py, 691 lines)
```python
class FilterChain:
    def add_filter(f) -> FilterChain  # chaining
    async def apply(url) -> bool     # concurrent filter application
```
Filters: URLPatternFilter, ContentTypeFilter, DomainFilter, URLFilter, SEOFilter, ContentRelevanceFilter

### Scorers (scorers.py, 518 lines)
```python
class CompositeScorer(URLScorer)     # weighted combination of scorers
class KeywordRelevanceScorer(URLScorer)  # keyword match ratio
class PathDepthScorer(URLScorer)     # proximity to optimal depth (default 3)
class ContentTypeScorer(URLScorer)   # extension/pattern weights
class FreshnessScorer(URLScorer)     # date-based freshness (YYYY in URL)
class DomainAuthorityScorer(URLScorer)  # domain weight map
```

---

## 3. DockerClient Python SDK (crawl4ai/docker_client.py, 219 lines)

```python
class Crawl4aiDockerClient:
    def __init__(self, base_url='http://localhost:8000', timeout=30.0,
                 verify_ssl=True, verbose=True, log_file=None)
    async def authenticate(self, email: str) -> None
    async def crawl(self, urls, browser_config=None, crawler_config=None,
                    hooks=None, hooks_timeout=30) -> Union[CrawlResult, List[CrawlResult], AsyncGenerator]
    async def get_schema(self) -> Dict[str, Any]
    async def close(self) -> None
```

Hooks support: accepts `Dict[str, Callable]` (converted to strings) or `Dict[str, str]` (pre-serialized).

---

## 4. Hooks System

### 8 Hook Points (async_crawler_strategy.py)
1. `on_browser_created(browser, context)`
2. `on_page_context_created(page, context)`
3. `on_user_agent_updated(page, context, user_agent)`
4. `before_goto(page, context, url)`
5. `after_goto(page, context, url, response)`
6. `on_execution_started(page, context)`
7. `before_retrieve_html(page, context)` — ideal for scrolling
8. `before_return_html(page, context, html)`

### Docker HookManager (deploy/docker/hook_manager.py)
- `UserHookManager(timeout=30)` — validates AST, compiles with safe builtins, executes with timeout
- `IsolatedHookWrapper` — wraps hooks so failures don't crash the main process

---

## 5. Docker API Endpoints (deploy/docker/server.py + api.py)

| Endpoint | Method | Description |
|----------|--------|-------------|
| / | GET | Redirect to playground |
| /token | POST | JWT auth |
| /crawl | POST | Crawl URLs, returns JSON |
| /crawl/stream | POST | NDJSON streaming |
| /crawl/job | POST | Async job submission |
| /crawl/job/<task_id> | GET | Job status/results |
| /md | POST | Markdown (fit/raw/bm25/llm filters) |
| /html | POST | Preprocessed HTML for schema |
| /screenshot | POST | Full-page PNG |
| /pdf | POST | PDF generation |
| /execute_js | POST | Execute JS on URL |
| /llm/{url:path} | GET | LLM Q&A on page (?q= required) |
| /schema | GET | Config schemas |
| /hooks/info | GET | Hook points + signatures + examples |
| /health | GET | Health check |
| /ask | GET | RAG context retrieval |
| /dashboard | static | Monitor UI |
| /playground | static | Interactive API testing |
| /metrics | GET | Prometheus metrics |
| /mcp/sse | SSE | MCP SSE transport |
| /mcp/ws | WS | MCP WebSocket transport |
| /mcp/schema | GET | MCP tool list |

---

## 6. MarkdownGenerator Options

`DefaultMarkdownGenerator(content_filter=None, options=None, content_source='cleaned_html')`
- Options: body_width=0, ignore_emphasis/links/images, protect_links, single_line_break, mark_code
- Output: `MarkdownGenerationResult(raw_markdown, markdown_with_citations, references_markdown, fit_markdown, fit_html)`

### Content Filters (content_filter_strategy.py)
- `BM25ContentFilter(user_query)` — scores chunks by relevance
- `PruningContentFilter()` — removes low-density DOM nodes
- `LLMContentFilter(llm_config, instruction)` — LLM selects relevant sections

### Chunking Strategies (chunking_strategy.py)
IdentityChunking, RegexChunking, NlpSentenceChunking, TopicSegmentationChunking, FixedLengthWordChunking, SlidingWindowChunking, OverlappingWindowChunking

---

## 7. Caching (CacheMode)

`CacheMode.ENABLED | DISABLED | READ_ONLY | WRITE_ONLY | BYPASS`
- `CacheContext(url, cache_mode, always_bypass)` — centralized cache decision logic
- Smart cache: ETag/Last-Modified/head fingerprint validation via `CacheValidator`

---

## 8. Session Management
- `create_session()` → `browser_manager.get_page(session_id)` → returns session_id
- `kill_session(session_id)` → removes from pool, closes page, decrements context refcount
- Config: `CrawlerRunConfig(session_id='...', js_only=True)`
- `js_only=True` skips `page.goto()` — only executes JS on existing page

---

## 9. JavaScript Execution
Config: `js_code` (str or list), `js_code_before_wait`, `js_only`, `wait_for`, `wait_until`, `delay_before_return_html`
Execution: `page.evaluate(script)` for each script in sequence. `js_execution_result` in CrawlResult.

---

## 10. AsyncUrlSeeder (async_url_seeder.py, 1,794 lines)

Features:
- Common-Crawl CDX streaming via httpx
- robots.txt → sitemap chain (nested indexes, .gz)
- Per-domain disk cache (~/.crawl4ai/<hash>.jsonl)
- HEAD-only liveness check
- Partial <head> parsing (title, meta, link)
- Rate-limiting via asyncio.Semaphore
- High concurrency (thousands on single event loop)

```python
config = SeedingConfig(
    source="cc",          # Common Crawl Index (CDX)
    pattern="*/blog/*",
    extract_head=True,
    query="python tutorials",
    scoring_method="bm25",
    score_threshold=0.5,
    max_urls=50
)
urls = await seeder.urls("example.com", config)
```

---

## 11. Advanced Usage Patterns

### Batch Crawling + Session Reuse
```python
from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher

dispatcher = MemoryAdaptiveDispatcher(
    memory_threshold_percent=80.0, check_interval=0.5, max_session_permit=10
)
results = await crawler.arun_many(urls=urls, config=CrawlerRunConfig(stream=False), dispatcher=dispatcher)
```

### Proxy Rotation
```python
from crawl4ai import ProxyConfig, RoundRobinProxyStrategy
proxies = [ProxyConfig(server="http://proxy1:8080"), ProxyConfig(server="http://proxy2:8080")]
strategy = RoundRobinProxyStrategy(proxies)
config = CrawlerRunConfig(proxy_rotation_strategy=strategy)
```

### Authentication/Cookies
- `storage_state` in `BrowserConfig` — dict or path to JSON cookie file
- `on_page_context_created` hook — navigate to login, fill forms
- `user_data_dir` in `BrowserConfig` — persistent browser profiles
- `session_id` — maintain session across multiple `arun()` calls

### Infinite Scroll / Virtual Scroll
```python
from crawl4ai import VirtualScrollConfig
config = CrawlerRunConfig(
    virtual_scroll_config=VirtualScrollConfig(
        container_selector="[data-testid='primaryColumn']",
        scroll_count=10, scroll_by="container_height", wait_after_scroll=1.0
    )
)
```

### Rate Limiting
```python
rate_limiter = RateLimiter(base_delay=(1.0, 3.0), max_delay=60.0, max_retries=3, rate_limit_codes=[429, 503])
dispatcher = SemaphoreDispatcher(max_session_permit=5, rate_limiter=rate_limiter)
```

---

## 12. Existing Crawl4AI MCP Implementations

### potterdigital/crawl4ai-mcp (most production-grade)
- **10 tools:** `ping`, `list_profiles`, `check_update`, `crawl_url`, `create_session`, `list_sessions`, `destroy_session`, `crawl_many`, `extract_structured` (LLM), `extract_css` (CSS), `deep_crawl`, `crawl_sitemap`
- **Singleton crawler** — one `AsyncWebCrawler` reused across all calls
- **Profile system** — YAML profiles with 3-layer merge
- **Session management** — named browser sessions with 30-min TTL
- **Output persistence** — `output_dir` switches from inline content to disk

### coleam00/mcp-crawl4ai-rag (2195 stars)
- **RAG pipeline:** crawl → chunk → OpenAI embed → Supabase pgvector → hybrid search → rerank
- **Contextual embeddings** — optional LLM-generated context wrapper per chunk
- **Knowledge graph** — Neo4j-backed repo structure extraction
- **Smart crawl** — auto-detects sitemap/txt/html and applies appropriate strategy

### walksoda/crawl-mcp (most tools, 17+)
- **17+ tools** including `intelligent_extract`, `extract_entities`, `search_google`, `batch_crawl`
- **Fallback cascading** — every tool has fallback strategy
- **Token limit enforcement** — 25K cap with emergency truncation

### laurentvv/crawl4ai-mcp (minimalist, 1 tool)
- **Single `crawl` tool** — deep BFS crawl, writes to `.md` file
- **Magic mode** — anti-bot bypass
- **JS security gate** — requires `CRAWL4AI_MCP_ALLOW_JS=true`

### sadiuysal/crawl4ai-mcp-server (agent-style)
- **4 tools:** `scrape`, `crawl`, `crawl_site`, `crawl_sitemap`
- **Adaptive crawling** — `should_continue_crawling()` heuristic
- **Run directory pattern** — run_id, manifest.json, per-page .md files

### Key Finding: NONE use remote Docker mode
All implementations create `AsyncWebCrawler` locally. Our MCP would be the first to use Crawl4AI as a remote service.

---

## 13. Crawl4AI Docker REST API — Request/Response Formats

### POST /crawl
```json
{
  "urls": ["https://example.com"],
  "browser_config": {"type": "BrowserConfig", "params": {"headless": true, "user_agent": "..."}},
  "crawler_config": {"type": "CrawlerRunConfig", "params": {
    "cache_mode": "bypass",
    "wait_until": "domcontentloaded",
    "delay_before_return_html": 2,
    "page_timeout": 30000,
    "extraction_strategy": {"type": "JsonCssExtractionStrategy", "params": {"schema": {...}}},
    "deep_crawl_strategy": {"type": "BestFirstCrawlingStrategy", "params": {"max_depth": 3, "max_pages": 50}}
  }}
}
```
Response: `{"results": [{"url": "...", "success": true, "markdown": {...}, "html": "...", "links": {...}, "extracted_content": "...", "status_code": 200}]}`

### POST /md
```json
{"url": "https://example.com", "f": "fit", "q": "main content", "c": "0"}
```
Filter modes: `raw`, `fit`, `bm25`, `llm`. Cache: `0`=bypass, `1`=use.

### POST /screenshot
```json
{"url": "https://example.com", "screenshot_wait_for": 2}
```
Response: PNG binary.

### POST /pdf
```json
{"url": "https://example.com"}
```
Response: PDF binary.

### POST /execute_js
```json
{"url": "https://example.com", "scripts": ["return document.title", "return document.querySelectorAll('a').length"]}
```

### GET /llm/<url>?q=<question>
Response: `{"answer": "...", "url": "..."}`

### POST /llm
```json
{"url": "https://example.com/article", "q": "Extract title, author, and main points", "provider": "openai/gpt-4o-mini"}
```
