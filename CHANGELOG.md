<!-- generated-by: gsd-doc-writer -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `KINDLY_MMR_LAMBDA` environment variable (default `0.5`, was `0.7`) for configuring the
  MMR relevance–diversity trade-off in the rerank diversity stage. Default lowered to `0.5`
  for balanced relevance/diversity per Carbonell & Goldstein (1998).
- `compact()` async method on `SemanticCacheStore` for LanceDB vacuum and index
  optimization (removes deleted rows, reclaims disk space).
- `KINDLY_HF_TOKEN` environment variable as the primary HuggingFace API token lookup
  in the embeddings service, before the generic `HF_TOKEN` / `HUGGINGFACEHUB_API_TOKEN`
  fallbacks.
- `__post_init__` validators in `Settings` for `mmr_lambda_param` (0–1 range),
  `jina_score_threshold` (0–1 range), and `rrf_k` (must be positive) to catch
  misconfiguration at startup.

### Changed

- **Jina reranker model**: upgraded from `jina-reranker-v1-base-en` to
  `jina-reranker-v2-base-multilingual`. Documents now sent as structured
  `{"text": snippet, "title": title}` dicts for better title/body separation.
  Module-level `httpx.AsyncClient` singleton replaces per-call client creation.
- **Semantic cache distance formula**: fixed L2→cosine conversion from
  `1.0 - min(distance, 1.0)` to `max(0.0, 1.0 - distance / 2.0)`. For unit-normalized
  embeddings LanceDB returns L2 distance ∈ [0, 2]; dividing by 2 gives the correct
  similarity range [0, 1].
- **Content type classification order**: reordered checks to TECHNICAL→FAQ→NEWS
  (was NEWS→TECHNICAL→FAQ) to prevent generic developer queries from matching news
  heuristics. Removed 6 overly broad news keywords (`breaking`, `latest`, `current`,
  `recent`, `happening`, `now`).
- **MMR score waterfall**: `max_jina_score` is now captured immediately after the Jina
  cross-encoder stage, before MMR diversity reordering, so the waterfall threshold is
  not incorrectly based on the diversity-sorted first result. Items with `score is None`
  (bi-encoder-only) are preserved through the filter.

### Fixed

- **SQL injection via `provider_key`**: LanceDB FTS and vector search queries
  interpolated `provider_key` directly into SQL WHERE clauses. Fixed by sanitizing
  with `re.sub(r"[^a-zA-Z0-9_\\-]", "", provider_key)` before interpolation.
- **FTS index silent rebuild**: `create_fts_index(replace=True)` was called
  unconditionally on every server start, silently dropping and rebuilding the full-text
  search index. Fixed to try `replace=False` first and only fall back to `replace=True`
  if creation fails (e.g., index already exists from a previous schema change).
- **O(n²) set removal in MMR diversity loop**: `remaining` was a `list` with O(n)
  `.remove()` calls inside an O(n) loop. Replaced with `set[int]` for O(1) removal.
- **Dead export**: `compute_embedding_diversity` was removed from `diversity.py` but
  still exported from `rerank/__init__.py`, causing an `ImportError` on startup. Removed
  from `__all__` and the import statement.

- `KINDLY_MMR_LAMBDA` environment variable (default `0.7`) for configuring the
  MMR relevance–diversity trade-off in the rerank diversity stage.
- `compute_embedding_diversity` now returns a `tuple[list[int], dict[int, float]]`
  containing the kept indices and a mapping of removed-index → actual cosine
  similarity score, enabling accurate telemetry for diversity removals.

### Changed

- **SearXNG configuration**: reduced engine suspension times from 24h to 1h (SearxEngineAccessDenied, SearxEngineCaptcha) and 30min (SearxEngineTooManyRequests) for faster recovery after temporary blocks. Cloudflare-specific suspensions reduced from 15 days to 1h.
- **SearXNG engines (RESEARCH-BACKED)**: replaced broken general engines (startpage, wikipedia) with independent index engines that own their crawlers and avoid CAPTCHA/rate-limit issues:
  - **mojeek** (own crawler MojeekBot, UK-based, no tracking) - weight 1.5
  - **marginalia** (DIY search, non-commercial focus) - weight 1.3
  - **wiby** (lightweight, small/non-commercial sites) - weight 1.2
  - **yandex** (own massive index, good for technical queries) - weight 1.4
  - Research source: GitHub Discussion #5651 reveals ALL major engines broken (Google down, Bing irrelevant, Brave rate-limited, DDG CAPTCHA). Alternative: 4get-hijacked repo with 35 working engines via curl-impersonate sidecar.
- **Removed workaround**: `SEARXNG_ENGINES` environment variable removed from .env - proper instance-level configuration now in settings.yml `keep_only` filter.
- **DDG provider mode**: added `KINDLY_DDG_MODE` environment variable (default `always`) for configurable DuckDuckGo provider mode, matching the pattern used by other providers. DDG is now always-on by default as a normal provider alongside SearXNG and Gemini.
- **Default results**: changed `KINDLY_DEFAULT_NUM_RESULTS` to `10` (was `5`) for more comprehensive search results by default.
- **Provider mode config simplified**: all provider mode defaults now live in ONE place (`settings.py`). Removed redundant fallback defaults from `search/__init__.py`. `.env` only contains overrides when needed, not duplicate defaults. Provider mode parsing uses single `_parse_mode()` function.
- **Composio provider**: changed default mode from "conditional" to "always" so it fires alongside SearXNG and Gemini.
- **HF Embeddings**: model changed from `BAAI/bge-m3` (1024-dim) to
  `ibm-granite/granite-embedding-97m-multilingual-r2` (384-dim) for faster inference.
  Default dimension updated to `384` (`KINDLY_EMBEDDING_DIM`). Semantic cache with
  incompatible dimensions must be cleared (`rm -rf ./lancedb_data`).
- **Semantic cache schema**: LanceDB semantic cache table/schema selection now
  follows the active embedding model and `KINDLY_EMBEDDING_DIM` instead of
  staying pinned to the old `BAAI/bge-m3` 1024-dim layout. This prevents
  `query dim(...) doesn't match the column embedding vector dim(...)` errors
  when the embedding backend changes.
- **HF Embeddings**: added circuit breaker (`HFCircuitBreaker`) with threshold of 3
  consecutive failures and 60-second recovery timeout. Opens on repeated timeouts/API
  errors to prevent cascading failures; `CircuitOpenError` raised when blocking calls.
- **Rerank pipeline**: embedding timeouts tightened for faster degradation.
  Query embedding timeout reduced from 30s to 15s (critical path). Diversity embedding
  timeout reduced from 60s to 10s (non-critical stage).
- **Gemini provider**: fixed provider naming mismatch. Results now tagged with
  `providers=["gemini"]` (matching registration name) instead of `["gemini-pollinations"]`,
  resolving RRF double-counting where both names appeared with equal counts.
- **Telemetry**: added `get_search_total_metric()` convenience function for direct
  counter access in `search_instrumented.py` (resolves ImportError on startup).
- **Provider selection**: fixed `should_fire()` to respect explicit `providers=[...]`
  as an allow-list. Previously, `ALWAYS` mode providers (SearXNG, DDG) would fire
  regardless of caller's explicit provider list, leaking into Composio/Jina tests.
  Now: `providers=["composio_llm_search"]` fires ONLY composio; `providers=[]`
  fires nothing; `providers=None` uses mode-based default selection.
- **Telemetry**: fixed `record_provider_call()` parameter mismatch in `search_instrumented.py`.
  Changed `status="success"` to `status_code=200` and `status="error"` to `status_code=500`
  to match the function signature. This TypeError was crashing all provider calls,
  causing empty search results.
- **Response metadata**: fixed `WebSearchResponse` to correctly populate `providers_used`
  and `total_results` fields. Previously these were defaulting to empty/0 because
  the orchestrator didn't aggregate provider names from merged results.
- **SearXNG Docker profile**: repo-managed `searxng-settings` now uses
  SearXNG `use_default_settings.engines.keep_only` to enforce the curated
  agent engine set instead of merging the full default catalog. The live
  profile is tightened to 15 engines: default general web, coding/Q&A,
  package/AI, and science sources that survived local SearXNG startup and
  query probes. Bing/Google are excluded after direct JSON tests showed Bing
  returning high-rank unrelated pages for exact technical queries; Brave is
  excluded because the local instance immediately receives upstream
  too-many-requests suspensions; Mojeek is excluded from the enabled profile
  because this local container received HTTP 403 and suspended the engine.
  Crossref is also excluded after live science probes timed out and produced
  SearXNG unresponsive-engine errors.
- **SearXNG Docker profile**: compose now uses `SEARXNG_VALKEY_URL` and
  `settings.yml` now uses `valkey.url`, avoiding the deprecated `redis.url`
  path for the Valkey cache.
- **Rerank pipeline**: query embedding is computed exactly once at the start of
  `rerank_results` and passed directly to Stage 1 (`bi_encoder_filter`) and
  Stage 3 (MMR diversity). Both stages require it as a mandatory argument —
  there is no internal re-embedding or optional fallback path. If the embedding
  call fails, both stages are skipped and the pipeline degrades to Jina-only.
- **`bi_encoder_filter`**: signature changed to accept `query_embedding:
  list[float]` as its first positional argument instead of `query: str`.
  The function no longer calls `embed_query` internally; it only embeds the
  candidate documents. Callers must always supply the pre-computed vector.
- **Rerank pipeline**: bi-encoder stage now filters to `top_k * 2` candidates
  (previously filtered straight to `top_k`), giving the Jina cross-encoder a
  richer pool to reorder before the final `top_k` slice.
- **Rerank pipeline**: diversity threshold now reads `settings.diversity_threshold`
  (`KINDLY_DIVERSITY_THRESHOLD`, default `0.85`) instead of a hardcoded `0.9`.
- **Rerank pipeline**: MMR `lambda_param` now reads `settings.mmr_lambda_param`
  (`KINDLY_MMR_LAMBDA`, default `0.7`) instead of a hardcoded literal.
- **Rerank pipeline**: diversity-removal telemetry now records the actual cosine
  similarity score for each removed result instead of a hardcoded placeholder.
- **Bi-encoder**: candidate text format changed from `"title snippet"` (space) to
  `"title\nsnippet"` (newline) to match the format used by the Jina reranker and
  diversity stages.
- **Search merge**: single-provider result lists are now always passed through
  `merge_search_results` so host-cap deduplication applies even when only one
  provider returned results.

### Added (previous work, carried forward)

- Documentation note for observed Composio Search toolkit caveats covering Composio Similarlinks, Composio Image Search, Composio LLM Search, and Composio Web Search.
- Configurable differentiated rate-limit settings:
  - `KINDLY_RATE_LIMIT_WEB_SEARCH_RPS` / `KINDLY_RATE_LIMIT_WEB_SEARCH_BURST` for cheap tools (`web_search`, `get_content`, `gemini_search`)
  - `KINDLY_RATE_LIMIT_EXPENSIVE_RPS` / `KINDLY_RATE_LIMIT_EXPENSIVE_BURST` for expensive tool (`perplexity_search`)
- Composio Search toolkit integration:
  - Added Composio LLM Search as a conditional `web_search` provider.
  - Added standalone `composio_similarlinks` and `composio_image_search` MCP tools.
  - Added Composio SDK adapter and configuration settings.
- Jina API reranking:
  - New `rerank/jina.py` client for `https://api.jina.ai/v1/rerank`
  - Default reranker model is `jina-reranker-v3` via `KINDLY_JINA_RERANK_MODEL`
  - Rerank result parsing uses Jina-returned `index` values, preserving duplicate document handling
- Hugging Face Inference Provider embeddings:
  - New `embeddings/hf_inference.py` backend using `AsyncInferenceClient.feature_extraction`
  - Default embedding model is `BAAI/bge-m3` on provider `hf-inference`
  - Embedding dimension is now configurable with default `KINDLY_EMBEDDING_DIM=1024`
- Provider-aware cache identity:
  - Exact query cache keys now include the normalized caller provider set
  - Search SingleFlight keys now include the normalized caller provider set
  - Semantic cache rows now store `provider_key`
- Provider mode system for controlling search provider behavior:
  - `ProviderMode` enum: `always`, `conditional`, `never`
  - `ProviderConfig` class with `should_fire()` method for mode-based selection
  - Provider registry pattern for dynamic provider registration
  - Environment variables: `KINDLY_TAVILY_MODE`, `KINDLY_BRAVE_MODE`, `KINDLY_JINA_MODE`, `KINDLY_GEMINI_SEARCH_MODE`
  - Default modes: Tavily/Brave=`never` (disabled), Jina=`conditional` (caller-requested), SearXNG/DDG/Gemini=`always`
- DuckDuckGo search provider (`search/ddg.py`) using `ddgs` library:
  - Free, reliable fallback provider
  - Uses `asyncio.to_thread` for blocking library compatibility
  - Registered as Tier 2 free provider alongside SearXNG
- Gemini provider overhaul for the `web_search` mix:
  - Gemini is now a standard provider in `web_search` when `KINDLY_GEMINI_API_KEY` is configured
  - Search-provider prompting is now separate from the standalone `gemini_search` MCP tool
  - Gemini provider results now derive snippets from `groundingSupports` / `groundingChunks` metadata instead of returning blank snippets
- Optional `providers` parameter on `web_search` tool:
  - Caller can specify which conditional providers to include in search mix
  - Standard providers (searxng, ddg, gemini) always fire when configured
- Resources for server status introspection:
  - `status://providers` - shows which search providers are configured
  - `status://features` - shows feature flags (semantic cache, query rewrite, reranking)
  - `docs://workflow` - recommended workflow for using web search tools
- Prompts for common workflows:
  - `debug_error_prompt` - guides error debugging with web search
  - `research_topic_prompt` - guides topic research workflow
  - `find_library_docs_prompt` - guides library documentation lookup
- Progress logging via `ctx.info()` calls in `web_search` and `get_content` tools

### Changed

- RRF merge now applies deterministic host/domain caps in the top results to reduce domain clustering while preserving strong candidates and encounter-order tie breaks.
- Rerank diversity is now always-on with true MMR host-aware diversification driven by HF embeddings (query-to-document and document-to-document cosine) (relevance + semantic novelty + host penalty) plus strict near-duplicate suppression to improve final result usability.

- Replaced the HF Space reranker path with Jina API reranking in the core rerank pipeline.
- Refactored server middleware setup to use differentiated per-tool token-bucket rate limiting instead of one global rate limit.
- Replaced the HF Space embedding path with Hugging Face Inference Provider embeddings.
- Semantic cache now uses a BGE-M3-specific 1024-dimension LanceDB table (`semantic_cache_hf_inference_BAAI_bge_m3_1024`) instead of the old 512-dimension table.
- Exact query cache now uses `query_cache_v2` to avoid reusing stale rows produced by provider-unaware cache keys.
- Tool annotations using `ToolAnnotations` class for all MCP tools:
  - `title` - human-readable tool names (e.g., "Web Search", "Get Content")
  - `readOnlyHint`, `idempotentHint`, `openWorldHint` - semantic hints for MCP clients
- Context injection using `CurrentContext` for `web_search` and `get_content` tools

## [0.1.8] - 2026-04-22

### Added

- YouTube transcript extraction:
  - New `youtube_transcript` tool for extracting captions/transcripts from YouTube videos
  - Supports multiple URL formats: watch, youtu.be, embed, shorts, live
  - Output formats: plain text, timestamped (`[MM:SS]`), or raw JSON segments
  - Language selection and translation support
  - Configurable timeout via `KINDLY_YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS`
  - Proxy support via `KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL` for cloud IP blocking
- YouTube video search:
  - New `youtube_search` tool for searching YouTube via SearXNG engine
  - Returns lightweight video metadata (title, link, snippet)
  - Designed for workflow: search → select → `youtube_transcript`
- Response models for YouTube:
  - `YouTubeTranscriptResponse` - transcript data with metadata
  - `YouTubeSearchResponse` - YouTube video search results
- Content resolver module for YouTube URL handling (`content/youtube.py`)
- Search module for YouTube-specific search (`search/youtube.py`)
- Middleware system for tool protection and guidance:
  - Rate limiting middleware (1 request per 2 seconds, burst capacity 3)
  - Expensive tool protection middleware for `perplexity_search`
  - Gemini advisory middleware (non-blocking, informational)
  - Query quality middleware with tips on every `web_search` call
  - Result guidance middleware for result interpretation

### Dependencies

- Added `youtube-transcript-api>=0.6.0` for transcript extraction

## [0.1.7] - 2026-04-21

### Added

- AI-powered search tools:
  - `gemini_search` - Gemini with Google Search grounding for quick, grounded answers
  - `perplexity_search` - Perplexity Sonar via Pollinations API for synthesized answers
- Structured output mode for `gemini_search` (executive_summary, key_findings, sources, confidence)
- Deep reasoning mode for `perplexity_search` (`depth="deep"` uses Sonar Reasoning)

### Changed

- `web_search` now returns lightweight results only (title, link, snippet)
- Separated search (`web_search`) from content extraction (`get_content`)
- Search results no longer include `page_content` field

## [0.1.6] - 2026-04-20

### Added

- Page cache for URL → content mapping
- Exact query cache for deterministic lookups
- Configurable tool timeout via `KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS`

### Changed

- Content resolution pipeline improvements for StackExchange, GitHub, Wikipedia, arXiv

## [0.1.5] - 2026-04-19

### Added

- Semantic caching with LanceDB for fuzzy query matching
- Bi-encoder and cross-encoder reranking pipeline
- Query rewrite with Mistral for expanded search coverage

## [0.1.4] - 2026-04-18

### Added

- Multi-provider search with RRF merge (SearXNG, Tavily, Brave, Jina)
- Specialized content extraction for StackExchange, GitHub Issues, Wikipedia, arXiv
- Universal HTML extraction with nodriver headless browser

## [0.1.0] - 2026-04-15

### Added

- Initial MCP server implementation
- Basic web search via SearXNG
- Simple content extraction with trafilatura
- FastMCP server with stdio and HTTP transports
