# get_content and batch_get_content improvement research

Date: 2026-05-12T08:23:23+02:00
Scope: research only. No runtime code changes in this pass.

## Executive summary

The current `get_content` implementation is a good baseline extractor: it routes known platforms through specialized APIs, then tries HTTP extraction, then browser fallback. The main gap is not "more scraping." The main gap is that the tool contract exposes only one output shape: full best-effort Markdown by URL.

The strongest external patterns point to adding explicit fetch profiles and context-shaped outputs:

1. Add bounded output controls: `max_chars` or `max_tokens`, `chunk`, `mode`, freshness/cache tolerance, and per-request timeout.
2. Add structured metadata: final URL, title, extraction method, word/char counts, truncated flag, cache status, content type, and per-URL status/error tags.
3. Add query-aware extraction modes: highlights/evidence snippets based on a query, not only whole-page Markdown.
4. Add an optional AI summary format, but do not replace raw content with summary-only output. Treat summaries as derived artifacts with their own cache key.
5. Upgrade `batch_get_content` from "parallel get_content" into a bounded batch content primitive: dedupe, preserve input mapping, expose per-item status, support output budgets, and optionally return an "evidence pack" instead of N full pages.

Recommended implementation direction:

- Keep `get_content(url)` backward compatible.
- Add optional parameters in phases rather than a parallel tool explosion.
- Add `mode="markdown" | "metadata" | "links" | "highlights" | "summary" | "evidence_pack"`.
- Add `batch_get_content(..., mode=..., query=..., max_chars_per_url=..., total_chars_budget=...)`.
- Cache raw extraction separately from profile-shaped outputs and AI summaries.

## Current implementation baseline

Observed in `src/kindly_web_search_mcp_server/server.py`:

- `get_content(url)` returns `{"url": str, "page_content": str, "diagnostics"?: list}`.
- `batch_get_content(urls, max_concurrency=3)` returns `{"results": [{"url", "page_content", "error"}]}`.
- `batch_get_content` hard-caps input to 10 URLs and clamps concurrency to 1-5.
- `batch_get_content` divides the global tool timeout by URL count with a minimum per-URL timeout of 10 seconds.
- Neither tool exposes `max_chars`, `mode`, `query`, selector, output format, freshness, summary, or metadata controls.

Observed in `content/resolver.py`:

- Resolver order is StackExchange API, GitHub Issues API, GitHub Discussions API, Wikipedia API, arXiv, HTTP extraction, browser HTML fallback.
- HTTP extraction uses trafilatura first, then html2text, then BS4/markdownify, then fallback markdown extraction.
- Generic PDFs are still not handled except arXiv.

Observed in `cache/page_cache.py`:

- Page cache is keyed by canonical URL hash only.
- Cached value stores `page_content`, extraction method, word count, created timestamp, TTL, and optional metadata.
- This is safe for raw canonical page extraction, but not safe for parameterized outputs unless output-affecting parameters are added to the key or stored in a separate derived-output cache.

Observed in `models.py`:

- `GetContentResponse` already has optional `content_type`, but `server.py` does not populate it.

## Evidence from local materials

### `materials/backend/tools.py`

Useful pattern:

- `read_url` exposes `chunk` and `use_html`.
- It caches by `url::text/html`, not just URL, which avoids mixing text and HTML outputs.
- It returns only one chunk of about 10,000 characters and tells the agent how many chunks exist.
- It has a stripped-down HTML mode for cases where text extraction loses structure, tables, images, or structured data.

Adaptation:

- Add chunk/window support to `get_content` or batch outputs.
- Cache output profiles separately: `url + mode + extraction_profile`.
- Add `mode="html"` or `mode="structure"` only if bounded and stripped, not raw unbounded HTML.

### `materials/backend/research.py`

Useful pattern:

- Simple trafilatura extraction is wrapped as a URL-reading tool and formatted as tool output.
- The implementation is lighter than the current repo, so it is not a replacement, only confirmation that trafilatura is a normal first-stage extractor.

Adaptation:

- Current repo already exceeds this with resolver specializations and browser fallback.

### `nymbo-tools-mcp-docker-qwen/Modules/AI_Deep_Research.py`

Useful pattern:

- Separates `cheap_fetch_url` from expensive Bright Data fetch.
- `cheap_fetch_url` accepts `max_chars` with validation.
- In-run semantic cache stores fetched content and metadata.
- Bright Data is explicitly framed as fallback for blocked, JS-heavy, or insufficient pages.

Adaptation:

- Add `max_chars` to native `get_content`.
- Add `fallback_used` or `extraction_method` to responses.
- If external fetch providers are added, expose them as `extractor="auto|native|browser|jina|firecrawl|brightdata"` or through a policy-gated fallback, not silently in the same URL-only cache entry.

### `nymbo-tools-mcp-docker-qwen/Modules/_brightdata_mcp.py`

Useful pattern:

- Wraps upstream tools with explicit validated schemas: `search_engine`, `scrape_as_markdown`, `search_engine_batch`, `scrape_batch`.
- `scrape_batch` validates 1-5 URLs, lower than this repo's 10 URL hard cap.
- Response envelope includes `tool`, `arguments`, `isError`, `content`, `structuredContent`, `semantic_cache`, and `error`.

Adaptation:

- Add a richer per-item batch envelope even if the public contract remains simple by default.
- Consider lowering default batch output budget rather than only limiting URL count.

## Evidence from external tools and docs

### Jina Reader

Jina Reader explicitly splits `Read` (`r.jina.ai`) from `Search` (`s.jina.ai`) and describes Reader as converting any URL to LLM-friendly input. Its README also states that `s.jina.ai` searches, fetches the top 5 results, visits each URL, and applies the same reader stack.

Most useful fetch controls:

- `x-respond-with`: markdown, html, text, screenshot, pageshot.
- `x-engine`: browser, curl, auto.
- `x-cache-tolerance` and `x-no-cache`.
- `x-target-selector` and `x-wait-for-selector`.
- `x-timeout`.
- `x-max-tokens` and `x-token-budget`.
- `x-respond-timing`: early HTML, visible content, mutation idle, resource idle variants.

Adaptation:

- Add a local equivalent of `fetch_profile`, not necessarily all headers.
- High-value minimum: `mode`, `max_chars` or `max_tokens`, `force_refresh`, `cache_tolerance_seconds`, `target_selector`, `wait_for_selector`, and `engine`.
- Jina also supports PDFs and office docs in the current README, which is a strong signal for a generic document-loader lane.

Source: https://github.com/jina-ai/reader/blob/main/README.md

### Tavily MCP

Klavis Tavily MCP exposes `tavily_extract` separately from search. The extract tool accepts `urls` as an array, `extract_depth` basic/advanced, `include_images`, `format` markdown/text, and `include_favicon`. It also exposes crawl and map as distinct tools.

Adaptation:

- `batch_get_content` should support a real batch extraction contract, not only "run get_content N times."
- Minimal Tavily-like additions: `format`, `extract_depth`, `include_images`, `include_favicon`.
- Keep site mapping/crawling separate from content extraction.

Source: https://github.com/Klavis-AI/klavis/blob/main/mcp_servers/tavily/server.py

### Firecrawl

Firecrawl's CLI and docs expose scrape formats including markdown, html, rawHtml, links, screenshot, json, images, summary, changeTracking, attributes, and branding. Its advanced scrape guide shows include/exclude tags, `onlyMainContent`, `waitFor`, `timeout`, and `parsers: ["pdf"]`.

Its Python SDK types include `ScrapeOptions` with formats, headers, include/exclude tags, only-main-content, timeout, wait_for, mobile, parsers, actions, proxy, cache age controls, and other execution controls.

Adaptation:

- Add `formats` or `mode` before adding new single-purpose tools.
- Add `links` and `metadata` modes because they are cheap and useful for agents.
- Add generic PDF parser support as a resolver stage before browser fallback.
- If summary is added, make it an explicit output format.

Sources:

- https://docs.firecrawl.dev/sdks/cli
- https://docs.firecrawl.dev/advanced-scraping-guide
- https://github.com/firecrawl/firecrawl/blob/main/apps/python-sdk/firecrawl/v2/types.py

### Exa contents retrieval

Exa's contents endpoint supports:

- Summary output, including structured JSON-shaped summary output.
- Highlights based on a query, with `numSentences` and `highlightsPerUrl`.
- Context string mode, where contents are joined into one LLM-ready block, with `maxCharacters`.
- Per-URL crawl statuses with error tags and HTTP status.
- Freshness controls via `maxAgeHours` and livecrawl timeout.

Adaptation:

- Add `mode="highlights"` for query-focused extraction.
- Add `mode="context"` or `mode="evidence_pack"` for batch output that is already shaped for LLM use.
- Add per-item statuses and error tags instead of only `error: str|null`.
- Add freshness/cache controls.

Sources:

- https://exa.ai/docs/reference/contents-retrieval
- https://exa.ai/docs/reference/livecrawling-contents

### Anthropic search-result context format

Anthropic's search-result blocks are relevant because they define how fetched content should be shaped for model citations:

- Each item has `source`, `title`, and text `content`.
- Long content should be split into logical text blocks for finer citation boundaries.
- Tool results can return search-result blocks, and pre-fetched batch content is explicitly a supported use case.
- Best practices include returning only the most relevant results to avoid context overflow.

Adaptation:

- Add a `mode="citation_blocks"` or `mode="evidence_pack"` response shape:
  - `source`
  - `title`
  - `content_blocks`
  - `metadata`
  - `extraction_method`
- This is better than returning a single huge `page_content` string when the caller wants to cite or synthesize.

Source: https://platform.claude.com/docs/en/build-with-claude/search-results

### OpenAI long-document summarization cookbook

OpenAI's cookbook example for long document summarization uses chunking and optional recursive summarization. It explicitly varies summary detail by changing the number of chunks and then summarizing each chunk.

Adaptation:

- AI summary fetch should use a map/reduce or recursive chunk summary for long content, not a single prompt over arbitrarily truncated text.
- Summary output should include detail level, chunk count, model, prompt version, and whether input was truncated.

Source: https://developers.openai.com/cookbook/examples/summarizing_long_documents

## AI summary fetch: recommended or not?

Recommended as an explicit optional derived format.

Not recommended as the default `get_content` behavior.

Why:

- Firecrawl exposes `summary` as a requested format, not as a silent replacement for markdown.
- Exa exposes summary/highlights/context as distinct content options.
- OpenAI summarization patterns require chunking/detail controls for long documents.
- Anthropic citation guidance favors source-attributed blocks and relevant content limits, not opaque summary-only blobs.

Recommended contract:

```python
get_content(
    url: str,
    mode: Literal["markdown", "summary", "highlights", "metadata", "links", "evidence_pack"] = "markdown",
    query: str | None = None,
    max_chars: int | None = None,
    summary_detail: Literal["brief", "standard", "detailed"] = "standard",
    include_raw_excerpt: bool = True,
)
```

Recommended summary response shape:

```json
{
  "url": "https://example.com",
  "final_url": "https://example.com/final",
  "title": "Example",
  "mode": "summary",
  "summary": "...",
  "supporting_excerpt": "...",
  "page_content": "...optional, only if requested/backward-compatible...",
  "metadata": {
    "summary_model": "configured-model",
    "summary_prompt_version": "v1",
    "summary_query": "optional query",
    "input_content_hash": "sha256...",
    "input_chars": 50000,
    "summarized_chars": 12000,
    "truncated": false,
    "chunk_count": 8,
    "extraction_method": "http_extract"
  }
}
```

Cache rule:

- Raw page cache key: canonical URL plus raw extraction profile.
- Summary cache key: raw content hash plus summary model plus prompt version plus summary query plus detail level.
- Do not store AI summaries in the existing URL-only `page_cache.page_content`, because a summary of the same URL changes when the query, model, detail level, or prompt version changes.

## Proposed phased implementation

### Phase 1: Make existing fetch output controllable

Add optional params while keeping current defaults:

```python
get_content(
    url: str,
    max_chars: int | None = None,
    mode: Literal["markdown", "metadata", "links"] = "markdown",
    force_refresh: bool = False,
    cache_tolerance_seconds: int | None = None,
)
```

Concrete behavior:

- `mode="markdown"` preserves current response but adds metadata.
- `max_chars` truncates with explicit `truncated=true`, `chars_returned`, and `chars_total`.
- `mode="metadata"` returns title, final URL, content type, extraction method, cache status, length, detected links count, and resolver route.
- `mode="links"` extracts page links from the HTML/browser result, bounded.

### Phase 2: Upgrade batch contract

Add:

```python
batch_get_content(
    urls: list[str],
    max_concurrency: int = 3,
    mode: Literal["markdown", "metadata", "links", "evidence_pack"] = "markdown",
    query: str | None = None,
    max_chars_per_url: int | None = None,
    total_chars_budget: int | None = None,
    dedupe: bool = True,
)
```

Concrete behavior:

- Dedupe canonical URLs internally.
- Preserve original input order and map duplicates to the same fetched result.
- Return per-item `status`, `error_tag`, `http_status`, `cache_status`, `extraction_method`, `chars_total`, `chars_returned`, `truncated`.
- Enforce total output budget across the batch.
- In `evidence_pack` mode, return compact source blocks rather than whole pages.

### Phase 3: Add query-aware content shaping

Add:

```python
mode="highlights"
query="..."
highlights_per_url=5
sentences_per_highlight=3
```

Implementation options:

- First pass: local extractive scoring over headings/paragraphs with existing reranker or embeddings.
- Later: optional provider-backed mode using Exa/Tavily/Jina/Firecrawl if configured.

This addresses context engineering directly: agents often need the relevant parts of 5 pages, not all text from 5 pages.

### Phase 4: Add AI summary as explicit derived mode

Add:

```python
mode="summary"
summary_query: str | None = None
summary_detail: Literal["brief", "standard", "detailed"] = "standard"
include_supporting_excerpt: bool = True
```

Behavior:

- Extract raw content first.
- Chunk if content exceeds model input budget.
- Summarize chunks, then synthesize final summary.
- Return summary metadata and source excerpts.
- Cache summary separately from page content.

### Phase 5: Add document/PDF lane

High-signal external pattern:

- Jina Reader handles PDFs and office docs.
- Firecrawl supports `parsers: ["pdf"]`.
- Current repo handles arXiv PDFs but generic PDFs return unsupported.

Implementation:

- Add a generic content-type branch before HTML extraction.
- Use PyMuPDF or MarkItDown for PDFs/Office docs after qualifying dependency fit.
- Keep max-pages/max-chars controls.

## Minimal adaptation list

Highest ROI:

1. `max_chars` and `truncated` metadata on `get_content`.
2. Populate `content_type`, `title`, `final_url`, `extraction_method`, `cache_status`, and `word_count`.
3. Add `batch_get_content(total_chars_budget, max_chars_per_url)`.
4. Add `mode="metadata"` and `mode="links"`.
5. Add `mode="highlights"` with `query`.
6. Add explicit `mode="summary"` only after raw extraction and derived-cache design exists.
7. Add generic PDF/document handling.

Do not do first:

- Do not silently replace `page_content` with summary.
- Do not store profile-shaped or summary output in the current URL-only page cache.
- Do not make `batch_get_content` unbounded just because it is useful.

## Acceptance criteria for implementation later

- Existing `get_content(url)` calls still return current `url` and `page_content`.
- New modes are optional and documented in tool descriptions.
- Batch results include stable per-item status and partial failure data.
- Cache keys distinguish raw extraction from summaries/highlights/profile outputs.
- Tests cover:
  - cache hit with max_chars/profile mismatch
  - batch duplicate URLs
  - total output budget
  - summary cache key differences by query/model/detail
  - generic PDF unsupported/supported behavior depending on dependency availability

