<!-- generated-by: gsd-doc-writer -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
