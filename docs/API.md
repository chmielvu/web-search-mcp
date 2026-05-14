<!-- generated-by: gsd-doc-writer -->
# API Reference

This document describes all MCP tools exposed by the kindly-web-search-mcp-server. Each tool is designed for specific use cases in web discovery, content extraction, and AI-powered search synthesis.

---

## Tool Routing Guidelines

Default tool selection strategy:

1. **Discovery** → `web_search` (rewrite=true by default)
2. **Single URL extraction** → `get_content`
3. **Multi-URL extraction (3+ URLs)** → `batch_get_content` with cursor pagination
4. **Quick grounded synthesis** → `gemini_search`
5. **Deep reasoning synthesis** → `perplexity_search` (use after refining to single-topic query)
6. **Video discovery** → `youtube_search` → `youtube_transcript`
7. **Related URL discovery** → `composio_similarlinks` from known good URL

---

## web_search

**Description:** Search the web and return lightweight results (title, link, snippet) without page content.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | `str` | Yes | — | Search query string. Prefer specific keywords; use exact error text in quotes for debugging. |
| `research_goal` | `str` | Yes | — | Context describing what information you seek and why. Include topic, relevant context (packages, versions), and intended use. |
| `num_results` | `int` | No | `5` | Number of results to return. Range 1-10. Recommended 3-7 for broad coverage. |
| `rewrite` | `bool` | No | `True` | Enable Mistral query expansion. Set `False` for exact literals (stack traces, quoted errors, URLs, versions, hashes, UUIDs). |
| `providers` | `list[str]` | No | `None` | Optional provider list. Standard providers (searxng, ddg, gemini) fire automatically. Conditional: `tavily`, `brave`, `jina`, `composio_llm_search`. |

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `query` | `str` | Original raw query. |
| `results` | `list[dict]` | Lightweight search hits with `title`, `link`, `snippet`, `domain`, `resource_type`, `provider_count`, `score`. |
| `total_results` | `int` | Count of results returned. |
| `providers_used` | `list[str]` | Providers that successfully returned results. |
| `warnings` | `list[dict]` | Partial failures from providers (rate limits, timeouts). |

**Example:**

```python
# Normal discovery search
web_search(
    query="FastAPI middleware TypeError",
    research_goal="Debug middleware error in production FastAPI app",
    num_results=5,
    rewrite=True
)

# Exact literal search (error message)
web_search(
    query="'TypeError: Object of type NoneType is not JSON serializable'",
    research_goal="Find exact fix for this serialization error",
    num_results=3,
    rewrite=False
)
```

**Notes:**

- Requires at least one configured search provider: `SEARXNG_BASE_URL`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, `JINA_API_KEY`, or `KINDLY_GEMINI_API_KEY`.
- Results merged via Weighted Reciprocal Rank Fusion (RRF, k=60).
- Use `provider_count` as agreement signal: higher values indicate multiple providers surfaced the same URL.
- Semantic cache enabled by default (`KINDLY_SEMANTIC_CACHE_ENABLED=true`).
- For deeper content, follow up with `get_content(link)` on selected results.

---

## get_content

**Description:** Fetch one URL with bounded windowing and structured status.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `url` | `str` | Yes | — | URL to fetch. |
| `char_offset` | `int` | No | `0` | Character offset into extracted source text. |
| `char_length` | `int` | No | `20000` | Maximum characters to return. Clamped to `KINDLY_GET_CONTENT_MAX_CHARS` (default 50000). |
| `summary_mode` | `str` | No | `"none"` | Summary mode: `none`, `brief`, or `detailed`. Uses Chutes API when requested. |
| `focus_query` | `str` | No | `None` | Optional focus query for summary generation. |

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `input_url` | `str` | Exact URL provided by caller. |
| `normalized_url` | `str` | Normalized URL for cache/deduplication. |
| `fetched_url` | `str` | Actual URL after redirects (if network fetch succeeded). |
| `status` | `str` | Fetch status: `success`, `partial`, `blocked`, `unsupported`, `error`. |
| `source_type` | `str` | Detected source family: `html`, `pdf`, `github_issue`, `wikipedia`, etc. |
| `fetch_backend` | `str` | Backend strategy: `safe_http_extract`, `jina_reader`, `browser_fallback`, etc. |
| `page_content` | `str` | Bounded Markdown/text window. |
| `window` | `dict` | Pagination metadata with `has_more` and `next_offset`. |
| `content_type` | `str` | Detected HTTP content type. |
| `error` | `dict` | Structured error payload for non-success statuses. |
| `summary` | `dict` | Optional summary when `summary_mode` is not `none`. |

**Example:**

```python
# Basic content fetch
get_content(url="https://fastapi.tiangolo.com/tutorial/middleware/")

# Paginated fetch for long content
result = get_content(url="https://docs.example.com/long-page", char_length=20000)
if result["window"]["has_more"]:
    continuation = get_content(
        url="https://docs.example.com/long-page",
        char_offset=result["window"]["next_offset"]
    )

# Fetch with summary
get_content(
    url="https://blog.example.com/article",
    summary_mode="brief",
    focus_query="key takeaways about React hooks"
)
```

**Notes:**

- Content resolution uses staged fallback pipeline: StackExchange API → GitHub Issues/Discussions → Wikipedia → arXiv → HTTP extraction → Universal HTML (nodriver).
- Page cache persists fetched content for subsequent requests.
- Total tool timeout: `KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS` (default 120s).
- Use `batch_get_content` for 3+ URLs instead of multiple individual calls.

---

## batch_get_content

**Description:** Fetch multiple URLs with structured status, budgets, and continuation cursor.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `urls` | `list[str]` | Yes | — | URLs to fetch. Duplicates normalized and deduplicated. Max 30 URLs. |
| `max_concurrency` | `int` | No | `4` | Parallel fetch limit. Capped at 8. |
| `per_item_char_length` | `int` | No | `8000` | Maximum characters per URL window. |
| `total_char_budget` | `int` | No | `120000` | Total characters budget across this page. Max 300000. |
| `cursor` | `str` | No | `None` | Continuation cursor from prior partial batch. |

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `results` | `list[dict]` | Per-URL structured results with `input_url`, `normalized_url`, `status`, `page_content`, `window`. |
| `total_requested` | `int` | Total URLs requested. |
| `total_returned` | `int` | URLs successfully returned in this page. |
| `total_chars_returned` | `int` | Total characters returned. |
| `has_more` | `bool` | Whether more URLs remain. |
| `cursor` | `str` | Continuation cursor for next page if `has_more=true`. |

**Example:**

```python
# Batch fetch with budget
result = batch_get_content(
    urls=[
        "https://github.com/fastapi/fastapi/issues/123",
        "https://stackoverflow.com/questions/456",
        "https://docs.example.com/guide"
    ],
    max_concurrency=4,
    per_item_char_length=8000,
    total_char_budget=120000
)

# Continue partial batch
if result["has_more"]:
    continuation = batch_get_content(
        urls=result["results"],  # Remaining URLs handled internally
        cursor=result["cursor"]
    )
```

**Notes:**

- Prefer over multiple `get_content` calls when fetching 3+ URLs.
- Budget enforcement prevents runaway content accumulation.
- Failures isolated per URL: one failure does not abort the batch.
- Cursor-based pagination for large URL sets.

---

## gemini_search

**Description:** Search with Gemini Google Search grounding for quick, grounded answers with inline citations.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | `str` | Yes | — | Search query for grounded answer generation. |
| `structured_output` | `bool` | No | `False` | Return structured JSON with `executive_summary`, `key_findings`, `sources`, `confidence`. |
| `research_goal` | `str` | No | `None` | Optional context to guide research focus. |

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `query` | `str` | Original search query. |
| `answer` | `str` | AI-synthesized answer with inline citations `[N]`. |
| `web_search_queries` | `list[str]` | Search queries used for grounding. |
| `grounding_chunks` | `list[dict]` | Grounding sources with citation metadata. |
| `structured_result` | `dict` | Structured output when `structured_output=True`. |
| `error` | `str` | Error message if search failed. |

**Example:**

```python
# Quick grounded answer
gemini_search(query="What are React 19's new features?")

# Structured output for reports
gemini_search(
    query="Compare FastAPI vs Flask for production APIs",
    structured_output=True,
    research_goal="Need decision matrix for choosing web framework"
)
```

**Notes:**

- Requires `KINDLY_GEMINI_API_KEY` environment variable.
- Provides inline citations with `[N]` notation referencing `grounding_chunks`.
- Use for quick factual answers; use `web_search` + `get_content` when you need to browse sources yourself.
- Structured output mode includes confidence levels and categorized findings.

---

## perplexity_search

**Description:** AI-powered web search using Perplexity Sonar models via Pollinations API. Returns synthesized answers with citations.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | `str` | Yes | — | Search query. Prefer refined single-topic queries. |
| `depth` | `str` | No | `"normal"` | Search depth: `normal` (Sonar, balanced) or `deep` (Sonar Reasoning, complex analysis). |
| `research_goal` | `str` | No | `None` | Optional context to guide answer synthesis. |

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `query` | `str` | Original search query. |
| `answer` | `str` | AI-synthesized answer with source citations. |
| `sources` | `list[str]` | Source URLs cited in the answer. |
| `model` | `str` | Perplexity model used (`sonar` or `sonar-reasoning`). |
| `error` | `str` | Error message if search failed. |

**Example:**

```python
# Normal depth synthesis
perplexity_search(
    query="What are the best practices for MCP server error handling?",
    depth="normal"
)

# Deep reasoning for complex topics
perplexity_search(
    query="Analyze trade-offs between semantic caching vs exact caching in search systems",
    depth="deep",
    research_goal="Need architectural decision for search cache design"
)
```

**Notes:**

- Requires `POLLINATIONS_API_KEY` environment variable.
- **Rate-limited resource:** First call returns steering message with query-writing tips. Refine query and retry.
- Use only after refining to a single-topic query; `web_search` is better for broad discovery.
- Returns AI-synthesized text, not URL lists like `web_search`.

---

## quick_web_search

**Description:** Quick web search using Composio SEARCH_WEB (Exa-backed). Returns synthesized answer with citations.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | `str` | Yes | — | Search query. Add qualifiers (year, region, platform) for better results. |

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `query` | `str` | Original search query. |
| `answer` | `str` | AI-synthesized narrative summary (may be vague; prioritize citations). |
| `citations` | `list[dict]` | Source citations with `title`, `url`, `snippet`. |
| `total_citations` | `int` | Count of citations returned. |

**Example:**

```python
quick_web_search(query="React 19 release date 2024")
```

**Notes:**

- Requires `COMPOSIO_API_KEY` + `KINDLY_COMPOSIO_USER_ID` environment variables.
- Only indexes publicly available content — no paywalled/private pages.
- **Prioritize citations** as primary evidence; the answer can be vague.
- Broad queries return generic content; add qualifiers for specificity.

---

## composio_similarlinks

**Description:** Find pages similar to a known URL using Composio Similarlinks (Exa-backed neural/keyword search).

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `url` | `str` | Yes | — | Source URL to find similar pages for. |
| `num_results` | `int` | No | `5` | Number of results. Range 1-20. |
| `search_type` | `str` | No | `"neural"` | Search type: `neural` (semantic similarity) or `keyword` (text matching). |
| `category` | `str` | No | `None` | Optional content category filter. |
| `include_domains` | `list[str]` | No | `None` | Domains to include in results. |
| `exclude_domains` | `list[str]` | No | `None` | Domains to exclude from results. |

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `url` | `str` | Source URL used for similarity search. |
| `results` | `list[dict]` | Related URLs with `title`, `link`, `score`. |
| `total_results` | `int` | Count of similar links returned. |

**Example:**

```python
# Find similar documentation pages
composio_similarlinks(
    url="https://fastapi.tiangolo.com/tutorial/middleware/",
    num_results=10,
    search_type="neural"
)

# Filter to specific domains
composio_similarlinks(
    url="https://docs.python.org/3/library/asyncio.html",
    include_domains=["docs.python.org", "realpython.com"],
    exclude_domains=["stackoverflow.com"]
)
```

**Notes:**

- Requires `COMPOSIO_API_KEY` + `KINDLY_COMPOSIO_USER_ID` environment variables.
- Results contain only `title`, `link`, `score` — no snippets.
- Use `get_content()` on selected links when page text is needed.
- Neural search finds semantically similar content; keyword search finds textually similar.

---

## youtube_search

**Description:** Search YouTube videos via SearXNG YouTube engine.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | `str` | Yes | — | Search query for YouTube videos. |
| `num_results` | `int` | No | `5` | Number of results. Range 1-20. |

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `query` | `str` | Original search query. |
| `results` | `list[dict]` | YouTube video results with `title`, `link`, `snippet`, `resource_type="youtube"`. |
| `total_results` | `int` | Count of video results. |

**Example:**

```python
youtube_search(query="FastAPI tutorial async", num_results=5)
```

**Notes:**

- Requires `SEARXNG_BASE_URL` configured.
- Uses SearXNG's YouTube engine filter for video-specific results.
- Recommended workflow: `youtube_search` → `youtube_transcript(video_id)`.

---

## youtube_transcript

**Description:** Retrieve transcript/captions from a YouTube video.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `video_id_or_url` | `str` | Yes | — | YouTube URL or video ID (11 chars). Supports: `watch?v=`, `youtu.be/`, `embed/`, `shorts/`, `live/`. |
| `language` | `str` | No | `None` | Preferred language code (e.g., `en`, `es`). Defaults to `en`. |
| `translate_to` | `str` | No | `None` | Target language for translation (e.g., `de`, `fr`). |
| `format` | `str` | No | `"text"` | Output format: `text` (plain), `timestamped` (`[MM:SS]` lines), `json` (raw segments). |

**Returns:**

| Field | Type | Description |
|-------|------|-------------|
| `video_id` | `str` | YouTube video identifier. |
| `video_url` | `str` | Canonical YouTube URL. |
| `title` | `str` | Video title (requires separate API call, often `None`). |
| `transcript_text` | `str` | Transcript content in requested format. |
| `language` | `str` | Language code of transcript. |
| `is_translated` | `bool` | Whether transcript was translated. |
| `duration_seconds` | `float` | Total video duration. |
| `transcript_segments` | `list[dict]` | Raw segments if `format="json". |
| `error` | `str` | Error message if transcript fetch failed. |

**Example:**

```python
# Plain text transcript
youtube_transcript(video_id_or_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")

# Timestamped transcript
youtube_transcript(
    video_id_or_url="dQw4w9WgXcQ",
    format="timestamped"
)

# Translated transcript
youtube_transcript(
    video_id_or_url="https://youtu.be/dQw4w9WgXcQ",
    language="en",
    translate_to="de"
)
```

**Notes:**

- Transcripts may be disabled or unavailable for some videos.
- Private, deleted, or age-restricted videos return errors.
- Cloud IPs (AWS/GCP/Azure) may be blocked; use `KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL`.
- Max transcript length: `KINDLY_YOUTUBE_TRANSCRIPT_MAX_CHARS` (default 50000).
- Recommended chain: `youtube_search(query)` → `youtube_transcript(video_id)`.

---

## Response Status Codes

All tools return structured status information:

| Status | Description |
|--------|-------------|
| `success` | Content fully retrieved. |
| `partial` | Content partially retrieved (truncated, budget limit). |
| `blocked` | Content blocked (paywall, auth required, robots.txt). |
| `unsupported` | Content type not supported (video, binary). |
| `error` | Fetch failed (network, timeout, parsing). |
| `timeout` | Request exceeded `KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS`. |

---

## Error Response Structure

All tools return MCP-compliant error responses:

```json
{
  "error": "Human-readable error message",
  "error_type": "rate_limit|auth|network|content|config|unknown",
  "isError": true,
  "action": "Actionable guidance for recovery",
  "provider": "Provider that caused the error",
  "status_code": 429,
  "retry_after": 60
}
```

---

## Environment Variables Required

| Tool | Required Variable(s) |
|------|---------------------|
| `web_search` | `SEARXNG_BASE_URL` or `TAVILY_API_KEY` or `BRAVE_API_KEY` or `JINA_API_KEY` or `KINDLY_GEMINI_API_KEY` |
| `gemini_search` | `KINDLY_GEMINI_API_KEY` |
| `perplexity_search` | `POLLINATIONS_API_KEY` |
| `quick_web_search` | `COMPOSIO_API_KEY` + `KINDLY_COMPOSIO_USER_ID` |
| `composio_similarlinks` | `COMPOSIO_API_KEY` + `KINDLY_COMPOSIO_USER_ID` |
| `youtube_search` | `SEARXNG_BASE_URL` |
| `youtube_transcript` | None (optional: `KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL` for cloud IPs) |
| `get_content` | Optional: `GITHUB_TOKEN` for better GitHub extraction |
| `batch_get_content` | Optional: `GITHUB_TOKEN` for better GitHub extraction |

---

## Rate Limiting

The server implements differentiated rate limiting:

| Tool Tier | RPS | Burst |
|-----------|-----|-------|
| Lightweight (`web_search`, `get_content`, `gemini_search`) | `KINDLY_RATE_LIMIT_CHEAP_RPS` (default 10) | `KINDLY_RATE_LIMIT_CHEAP_BURST` (default 20) |
| Expensive (`perplexity_search`) | `KINDLY_RATE_LIMIT_EXPENSIVE_RPS` (default 2) | `KINDLY_RATE_LIMIT_EXPENSIVE_BURST` (default 4) |

Configure via environment variables to adjust limits for your deployment.

---

## Resources

The server exposes MCP resources for status checking:

| Resource URI | Description |
|--------------|-------------|
| `status://providers` | Which search providers are configured (SearXNG, Tavily, Brave, Jina, Composio, Gemini, Perplexity). |
| `status://features` | Feature flags status (semantic cache, query rewrite, reranking). |
| `docs://workflow` | Recommended workflow guide for using tools together. |

---

## Prompts

Built-in MCP prompts for common workflows:

| Prompt Name | Description |
|-------------|-------------|
| `debug_error_prompt(error_message)` | Guide for debugging errors using exact-literal search. |
| `research_topic_prompt(topic, depth)` | Guide for comprehensive topic research. |
| `find_library_docs_prompt(library, feature)` | Guide for finding library documentation. |