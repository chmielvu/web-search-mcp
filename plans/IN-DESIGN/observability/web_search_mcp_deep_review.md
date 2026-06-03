1. # Deep review of your personal web-search MCP

I reviewed the attached Repomix bundle as a codebase, not just as a surface description. I also compared it against current MCP/FastMCP patterns and current search/retrieval provider capabilities. I mostly ignored enterprise/security hardening per your instruction and focused on reliability, research quality, agent ergonomics, and quick wins.

## Executive summary

Your MCP is **substantially more advanced than a typical “web search MCP wrapper.”** It is closer to a small search/research platform:

| Area               | Assessment                                                                                                                                         |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| MCP fit            | Strong. It uses tools, resources, prompts, progress updates, typed schemas, and status resources.                                                  |
| Search quality     | Strong foundation: multi-provider search, query rewrite, weighted RRF merge, rerank, diversity pruning.                                            |
| Content extraction | Strong and unusually broad: GitHub, StackExchange, Wikipedia, arXiv, PDFs, generic HTML, Jina/browser fallback.                                    |
| Agent UX           | Good, but the tool surface is large. Better profiles/routing would help agents choose correctly.                                                   |
| Freshness          | Mixed. Search providers can be fresh, but cache behavior and query classification can make “latest/current” queries stale.                         |
| Maintainability    | Good module spread, but `server.py` is too large and contains several cross-cutting concerns.                                                      |
| Observability      | Very good for a personal tool. One tracing indentation bug/gap should be fixed.                                                                    |
| Biggest quick wins | Fix cache/filter bugs, content cache option leakage, Gemini env mismatch, freshness TTL, provider status visibility, and add a small eval harness. |

My overall evaluation: **8/10 as a personal research MCP**, with the biggest opportunities in **cache correctness, freshness-aware routing, provider-specific behavior, and simplifying the agent-facing interface**.

---

# 1. What I found in the attached codebase

I inspected **134 Python files**, roughly **30.6k lines of code**. The largest areas are:

| Area                    | Approx. LOC | Role                                                                                   |
| ----------------------- | ----------: | -------------------------------------------------------------------------------------- |
| `search/`               |       ~8.4k | Provider registry, query rewrite, search orchestration, merge, health, instrumentation |
| top-level / `server.py` |       ~7.6k | MCP tool registration, resources, prompts, orchestration glue                          |
| `content/`              |       ~3.8k | Fetching, extraction, caching, markdown conversion                                     |
| `scrape/`               |       ~3.1k | Browser/fetch support                                                                  |
| `analytics/`            |       ~2.0k | Query/tool analytics                                                                   |
| `agent/`                |       ~1.5k | Agentic research tooling                                                               |
| `cache/`                |       ~1.5k | Exact/semantic/page cache                                                              |
| `rerank/`               |       ~0.9k | Voyage/Jina reranking, MMR, embeddings                                                 |

The project is architecturally ambitious: it combines a search engine multiplexer, an extraction layer, an MCP interface, an analytics layer, agentic workflows, cache layers, and reranking.

---

# 2. Current architecture

## MCP surface

Your main server is `kindly-web-search`, built with FastMCP. It exposes:

### Core tools

| Tool                                  | Purpose                                      |
| ------------------------------------- | -------------------------------------------- |
| `web_search`                          | Main multi-provider URL discovery tool       |
| `get_content`                         | Fetch/read one URL with pagination/windowing |
| `batch_get_content`                   | Fetch multiple URLs within a char budget     |
| `discover_links`                      | Extract outbound/sitemap links               |
| `academic_search`                     | Scholarly search                             |
| `youtube_search`                      | Find videos                                  |
| `youtube_transcript`                  | Extract transcript text                      |
| `gemini_search`                       | Grounded synthesis                           |
| `perplexity_search`                   | Deeper synthesized answer                    |
| `grok_search`                         | Web/social synthesis                         |
| `quick_web_search`                    | Composio/Exa-style quick synthesis           |
| `composio_similarlinks`               | Expand from a known URL                      |
| `composio_image_search`               | Image search                                 |
| `agentic_web_research`                | Agentic research mode                        |
| `analytics_query`, `analytics_report` | Tool/search analytics                        |

### Resources

You already expose useful MCP resources:

| Resource             | Purpose                               |
| -------------------- | ------------------------------------- |
| `status://providers` | Configured provider and health status |
| `status://features`  | Feature flags, cache and timeout info |
| `docs://workflow`    | Recommended research workflow         |

### Prompts

You expose prompts for planning, result evaluation, gap analysis, and tool suggestion. This is a good match for MCP’s model: servers can expose **tools, resources, and prompts**, while clients can use capabilities such as progress reporting, logging, and context-aware interactions. The current MCP spec describes MCP as a JSON-RPC protocol with servers exposing tools/resources/prompts, plus utilities like progress, cancellation, logging, and capability negotiation. ([modelcontextprotocol.io][1]) FastMCP’s tool model also maps Python signatures/type annotations into MCP tool schemas, and its context supports logging and progress reporting, which you are already using. ([gofastmcp.com][2])

**Verdict:** MCP integration is above average. The main issue is not missing MCP features; it is that the tool surface is broad enough that clients may choose expensive or synthesized tools too early.

---

# 3. Search pipeline evaluation

## Current flow

Your main `web_search` flow is roughly:

1. Normalize query.
2. Build search options.
3. Check exact cache.
4. Check semantic cache.
5. Optionally rewrite/decompose query.
6. Run multiple providers.
7. Merge via weighted reciprocal rank fusion.
8. Rerank.
9. Apply pagination/windowing.
10. Cache result.
11. Return lightweight hits.

That is a solid architecture.

## Query rewriting

You have a good precision-aware rewrite policy. The code correctly tries to avoid rewriting:

* URLs
* quoted strings
* stack traces / error codes
* package versions
* hashes
* IPs
* constants
* CLI flags
* repo syntax
* `site:` / `filetype:` filters

This is a major quality win. A common failure mode in search agents is paraphrasing exact errors or versions into useless queries. Your bypass policy directly addresses that.

## Merge strategy

Weighted reciprocal rank fusion is a strong choice for combining heterogeneous providers. Your implementation also canonicalizes URLs and caps per-host results, which reduces duplicate/SEO-heavy result pages.

**Good design choice:** treating `provider_count` as an agreement signal is useful for agents. Your prompts also instruct the agent to treat `provider_count >= 2` as stronger evidence.

## Reranking

You use a modern reranking stack:

* query embedding
* optional bi-encoder prefilter
* Voyage primary reranker
* Jina fallback
* recency bonus
* MMR-style diversity

Voyage’s rerank API is designed around sending a query plus a list of documents and returning relevance scores/top-k; the current docs recommend `rerank-2.5` or `rerank-2.5-lite`, with limits around query/document counts and token budgets. ([Voyage AI][3]) So your choice of Voyage as primary reranker is sensible.

**Main limitation:** you rerank mostly on title/snippet/URL. That is cheap and often enough, but it can fail when snippets are thin, generic, or SEO-shaped. A high-impact later improvement would be optional **deep reranking**: fetch top 10–20 lightweight page excerpts, then rerank on real content.

---

# 4. Provider comparison

## SearXNG

SearXNG is a good personal default because it can aggregate many engines and remain self-hostable. Your code also handles SearXNG-specific options like categories, engines, language, page number, time range, and safesearch.

Important comparison point: SearXNG’s JSON/CSV/RSS formats must be enabled in `settings.yml`; otherwise API requests for disabled formats can return `403`, and its advanced query syntax is not guaranteed to be honored by every underlying engine. ([docs.searxng.org][4]) Your code already has helpful SearXNG diagnostics, which is good.

**Gap:** provider-specific filters should be more explicit. `site:` suffixes are okay for keyword engines, but neural/LLM-style providers may not respect them consistently.

## DuckDuckGo / DDGS

DDG is a useful no-key fallback. It should be treated as opportunistic, not as a guaranteed reliable backbone. In your setup, that is mostly how it behaves.

**Recommendation:** keep DDG enabled for personal use, but do not over-weight it in RRF when better providers are available.

## Brave

Brave Search is valuable because it is backed by Brave’s independent web index. ([Brave][5]) Brave also has rich vertical/enrichment capabilities for categories like sports, stocks, weather, and other real-time objects through its rich callback feature. ([Brave][6])

**Gap:** your Brave provider appears to use it as a standard web-results source. That is fine, but you are not really exploiting Brave’s richer vertical data.

**Quick win:** add a “fresh/current query” route that prefers Brave when the query contains terms like `today`, `latest`, `current`, `stock`, `weather`, `score`, `release`, `breaking`, or a date.

## Tavily

Tavily is particularly agent-oriented. Its docs expose `auto_parameters`, which can adjust search depth based on query intent, while explicit settings override the automatic choice. Tavily also has `include_answer`, `include_raw_content`, and `max_results`, with advanced search consuming more credits. ([docs.tavily.com][7])

**Gap:** your registry defaults Tavily to “never” unless requested. For personal use that is okay if cost control matters, but it leaves quality on the table for complex web research.

**Quick win:** enable Tavily conditionally for research-like queries, not for every query. For example:

* use Tavily for “compare”, “review”, “best”, “limitations”, “alternatives”, “research”
* avoid Tavily for exact errors, docs lookup, package signatures, and URLs

## Exa

You use Composio/Exa-like features through `quick_web_search` and `similarlinks`, but I did not see a native Exa provider in the main multi-provider merge. Exa’s API is relevant because it can search and extract contents from results in a single endpoint, and it is designed for semantic/neural search over natural-language queries. ([Exa][8])

**Gap:** native Exa support would be valuable for conceptual queries, “find pages similar to this,” and queries where keyword engines underperform.

**Recommendation:** add Exa as a conditional provider in `web_search`, with separate modes:

* `exa_search`: semantic discovery
* `exa_contents`: discovery + extracted highlights/content
* `exa_similar`: URL expansion

## Gemini / grounded search

Your separate `gemini_search` tool is useful for quick synthesis. Google’s Gemini grounding docs position Google Search grounding as a way to connect models to real-time web content and provide verifiable source links beyond model cutoff. ([Google AI for Developers][9])

**Good design choice:** keeping synthesized answer tools separate from raw URL discovery is smart. Agents should discover URLs first when they need evidence, and use Gemini/Perplexity/Grok for synthesis when appropriate.

**Bug/gap:** there is a config mismatch. Your `web_search` docstring says Gemini provider requires `KINDLY_GEMINI_API_KEY`, but the provider registry appears to use `POLLINATIONS_API_KEY` for the `gemini` provider, while `gemini_search` uses `KINDLY_GEMINI_API_KEY`. This will cause confusion and false “not configured” diagnoses.

---

# 5. Content extraction evaluation

Your content pipeline is strong. It handles:

* StackExchange API
* GitHub issues
* GitHub discussions
* Wikipedia
* arXiv metadata/PDF
* generic HTTP fetch
* PDF extraction
* HTML-to-Markdown conversion
* Jina reader fallback
* browser fallback

This is much better than a simple `requests + BeautifulSoup` reader. Jina’s reader stack is especially relevant for robust HTML-to-Markdown extraction, and its docs describe ReaderLM-v2 as a higher-quality HTML-to-Markdown path with higher token cost. ([jina.ai][10])

## Strong choices

* Windowing via `char_offset` and `char_length`
* Batch fetch with total char budget
* Cursor continuation for batch content
* Optional metadata and links
* Specialized resolvers before generic scraping
* Browser fallback only when cheaper methods fail

## Main content bug: page cache identity is too coarse

The page cache appears keyed primarily by normalized URL. That means calls with different options can contaminate each other:

Example failure mode:

1. Agent calls:

   ```python
   get_content(url, include_links=False)
   ```

   The URL is cached without links.

2. Later agent calls:

   ```python
   get_content(url, include_links=True)
   ```

   It can receive the cached no-links artifact.

Another failure mode:

1. User calls with `strip_selectors=["nav", "footer"]`.
2. Later call without stripping may still receive stripped content.

**Recommendation:** split cache into two layers:

| Cache layer              | Key                                              | Stores                                      |
| ------------------------ | ------------------------------------------------ | ------------------------------------------- |
| Raw fetch cache          | canonical URL + fetch mode                       | HTTP/PDF/browser raw artifact               |
| Derived extraction cache | raw artifact id + extraction options fingerprint | markdown, metadata, links, stripped content |

At minimum, include a small fingerprint of extraction-affecting options in the cache key:

```python
content_cache_fingerprint = {
    "include_links": include_links,
    "include_metadata": include_metadata,
    "max_links": max_links,
    "strip_selectors": sorted(strip_selectors or []),
}
```

---

# 6. Caching and freshness

Your caching is ambitious:

* exact query cache
* semantic cache
* TTL by content/query type
* page cache
* in-flight request coalescing

This is excellent for personal speed and API cost.

## Biggest search cache bug: domain filters/boosts do not apply on cache hits

In `web_search`, `domain_boost` and `domain_block` are applied after `_execute_search` returns. But exact/semantic cache hits return earlier from inside the search execution path. Also, your search identity key includes providers and search options, but not `domain_boost` / `domain_block`.

That means these calls can collide:

```python
web_search("python logging", domain_block=["medium.com"])
web_search("python logging")
web_search("python logging", domain_boost=["docs.python.org"])
```

Depending on cache state, the returned result ordering/filtering may not match the request.

**Priority:** high.

**Fix options:**

Option A, simplest:

* include `domain_boost` and `domain_block` in the search identity/cache key.

Option B, better:

* cache the unfiltered candidate pool
* always apply domain boost/block after retrieving from cache and before returning

For personal use, Option B gives better cache reuse and correct behavior.

## Freshness issue

Your semantic cache TTL logic can classify technical queries as long-lived. That is good for stable docs, but dangerous for queries like:

* “latest Python release”
* “current LangChain API”
* “OpenAI SDK breaking changes”
* “MCP latest transport”
* “today Tavily pricing”
* “FastMCP current docs”

The query classifier appears to check technical patterns before freshness/news patterns, so “latest FastMCP API” can become “technical” with a longer TTL.

**Quick fix:** add a `freshness_required` detector before content type classification:

```python
FRESHNESS_TERMS = {
    "latest", "current", "today", "yesterday", "this week", "2026",
    "release", "changelog", "breaking change", "deprecated",
    "pricing", "quota", "version", "migration"
}
```

Then:

* bypass semantic cache, or
* use very short TTL, or
* include date bucket in cache key, e.g. `YYYY-MM-DD`.

---

# 7. Observability and analytics

This is one of the stronger parts of your codebase.

You track:

* tool requests/responses
* provider health
* cache hits/misses
* result counts
* providers used
* warnings
* research goal
* spans/attributes
* analytics queries/reports

FastMCP’s context model supports logging and progress reporting, and your use of `ctx.report_progress()` aligns well with that. ([gofastmcp.com][11])

## Gap: content pipeline tracing indentation

In `content/fetch_pipeline.py`, the root tracing span appears to wrap only the initial StackExchange block due to indentation, while later resolver stages execute outside the intended span.

**Quick fix:** reindent the whole fetch pipeline under:

```python
with _content_tracer.start_as_current_span("content.fetch_pipeline") as span:
    ...
```

Or better: add stage-level spans:

```python
content.stackexchange
content.github_issue
content.github_discussion
content.wikipedia
content.arxiv
content.http_fetch
content.pdf_extract
content.html_extract
content.jina_fallback
content.browser_fallback
```

This would make debugging extraction failures much easier.

---

# 8. Tool UX and agent ergonomics

Your prompts are good and detailed. They teach the client when to use `web_search`, `get_content`, `batch_get_content`, `gemini_search`, `perplexity_search`, etc.

The problem is the number of tools. For personal use, this is manageable, but models can still over-call expensive or synthesized tools.

## Recommendation: add tool profiles

Add an environment variable:

```bash
KINDLY_TOOL_PROFILE=core
```

Suggested profiles:

| Profile    | Exposed tools                                                                         |
| ---------- | ------------------------------------------------------------------------------------- |
| `minimal`  | `web_search`, `get_content`, `batch_get_content`                                      |
| `core`     | minimal + `academic_search`, `youtube_search`, `youtube_transcript`, `discover_links` |
| `research` | core + `gemini_search`, `perplexity_search`, `grok_search`                            |
| `full`     | everything, including Composio, analytics, agentic research                           |

This reduces tool-selection noise without deleting functionality.

## Recommendation: add a cheap `search_status` tool

You already have `status://providers`, but many clients/tools use tools more naturally than resources.

Add:

```python
@mcp.tool(...)
def search_status() -> dict:
    ...
```

Return:

```json
{
  "providers": {
    "searxng": {"configured": true, "mode": "always", "healthy": true},
    "brave": {"configured": false, "mode": "conditional"},
    "tavily": {"configured": true, "mode": "conditional"}
  },
  "features": {
    "rewrite": true,
    "rerank": true,
    "semantic_cache": true
  }
}
```

This will help agents self-diagnose before retrying blindly.

---

# 9. Priority gap list

## P0 — correctness fixes

| Issue                                                 | Why it matters                                           | Action                                                                 |
| ----------------------------------------------------- | -------------------------------------------------------- | ---------------------------------------------------------------------- |
| `domain_boost` / `domain_block` ignored on cache hits | Results can violate user’s explicit domain request       | Include filters in cache key or apply filters after every cache return |
| Page cache ignores extraction options                 | `include_links`, metadata, stripped content can be wrong | Add extraction option fingerprint or split raw/derived cache           |
| Gemini env mismatch                                   | Confusing setup and false provider status                | Standardize env vars and docs                                          |

## P1 — freshness and quality wins

| Issue                                                    | Why it matters                                                | Action                                                      |
| -------------------------------------------------------- | ------------------------------------------------------------- | ----------------------------------------------------------- |
| Technical queries with “latest/current” can get long TTL | Stale answers for exactly the queries where freshness matters | Add freshness detector before semantic cache classification |
| Provider-specific filtering is generic                   | `site:` suffix may not work for all providers                 | Add native allowed/excluded domain support per provider     |
| Reranking only sees snippets                             | Thin snippets can mislead ranker                              | Add optional deep rerank using fetched excerpts             |
| Default provider routing is static                       | Cost/quality tradeoff is not query-aware                      | Add adaptive provider profiles                              |

## P2 — maintainability and polish

| Issue                                  | Why it matters                              | Action                                                                      |
| -------------------------------------- | ------------------------------------------- | --------------------------------------------------------------------------- |
| `server.py` is too large               | Harder to test and modify                   | Split into `tools/search.py`, `tools/content.py`, `tools/academic.py`, etc. |
| Duplicate provider health concepts     | Debugging confusion                         | Consolidate circuit breaker / health tracker reporting                      |
| No obvious test suite in bundle        | Regressions likely in cache/search behavior | Add focused pytest tests                                                    |
| Analytics not yet used as eval harness | Missed opportunity                          | Turn analytics into search-quality evaluation loop                          |

---

# 10. Recommended quick wins

## Quick win 1: fix search cache + domain filters

Best behavior:

1. Cache base result set.
2. On every return path, apply:

   * `domain_block`
   * `domain_boost`
   * `result_offset`
   * `num_results`

This lets the cache store reusable candidates while preserving user-specific result shaping.

Pseudo-shape:

```python
base_response = await get_from_cache_or_search(...)

filtered_results = _apply_domain_filters(
    base_response["results"],
    domain_boost=domain_boost,
    domain_block=domain_block,
)

windowed_results = filtered_results[result_offset : result_offset + num_results]

return {
    **base_response,
    "results": windowed_results,
    "result_window": {
        "offset": result_offset,
        "count": len(windowed_results),
        "total_candidates": len(filtered_results),
        "has_more": result_offset + num_results < len(filtered_results),
    },
}
```

## Quick win 2: fix page cache key

Add a derived extraction key:

```python
def extraction_fingerprint(
    *,
    include_metadata: bool,
    include_links: bool,
    max_links: int,
    strip_selectors: list[str] | None,
) -> str:
    payload = {
        "include_metadata": include_metadata,
        "include_links": include_links,
        "max_links": max_links,
        "strip_selectors": sorted(strip_selectors or []),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:16]
```

Then use:

```python
cache_key = f"{canonical_url}:{extraction_fingerprint(...)}"
```

Longer term, split raw and derived caches.

## Quick win 3: standardize Gemini config

Pick one scheme:

```bash
KINDLY_GEMINI_API_KEY=...
POLLINATIONS_API_KEY=...
```

Then clearly separate:

| Provider/tool                     | Env var                    |
| --------------------------------- | -------------------------- |
| `gemini_search` official Gemini   | `KINDLY_GEMINI_API_KEY`    |
| Pollinations-backed provider      | `POLLINATIONS_API_KEY`     |
| `web_search` Gemini-like provider | whichever it actually uses |

Right now the docstring and registry do not fully agree.

## Quick win 4: add freshness-aware cache bypass

Add:

```python
def is_freshness_sensitive(query: str) -> bool:
    q = query.lower()
    return any(term in q for term in FRESHNESS_TERMS)
```

Then:

```python
if is_freshness_sensitive(query):
    skip_semantic_cache = True
    exact_cache_ttl = min(existing_ttl, 900)
```

This is one of the highest-value personal-use improvements.

## Quick win 5: expose provider status in every search response

Add an optional diagnostic block:

```json
"provider_status": {
  "searxng": {"attempted": true, "ok": true, "results": 8},
  "ddg": {"attempted": true, "ok": false, "warning": "..."},
  "brave": {"attempted": false, "reason": "not_configured"},
  "tavily": {"attempted": false, "reason": "conditional_not_requested"}
}
```

This reduces confusion when a query returns weak results.

## Quick win 6: add focused tests

Start with these tests:

| Test                           | Expected behavior                                           |
| ------------------------------ | ----------------------------------------------------------- |
| cache + `domain_block`         | blocked domain never appears, even on cache hit             |
| cache + `domain_boost`         | boosted domain moves forward, even on cache hit             |
| page cache + `include_links`   | second call with links returns links                        |
| page cache + `strip_selectors` | stripped and unstripped calls do not contaminate each other |
| query rewrite bypass           | exact errors/versions/URLs are not rewritten                |
| RRF merge                      | duplicate URLs merge and `provider_count` increments        |
| freshness classifier           | “latest/current/today/version” queries use short TTL        |
| SearXNG 403                    | returns clear config warning                                |

---

# 11. Larger upgrades

## Upgrade 1: adaptive provider routing

Instead of static provider modes, classify query intent:

| Query type                | Preferred providers                                |
| ------------------------- | -------------------------------------------------- |
| exact error / stack trace | SearXNG, DDG, GitHub, StackExchange                |
| official API/docs         | SearXNG with docs domains, Brave, Jina reader      |
| current news / releases   | Brave, Tavily, SearXNG time range                  |
| conceptual research       | Tavily, Exa, Gemini synthesis                      |
| similar pages             | Exa/Composio similarlinks                          |
| academic                  | Semantic Scholar, arXiv, PubMed, Crossref/OpenAlex |
| code issues               | GitHub GraphQL, StackExchange, SearXNG             |

This would reduce cost and improve quality.

## Upgrade 2: native Exa provider

Add Exa directly to `web_search`, not only through Composio. Exa is a good fit for semantic queries and can search plus retrieve contents from results. ([Exa][8])

Suggested modes:

```bash
KINDLY_PROVIDER_EXA_MODE=conditional
```

Use it when:

* query is conceptual
* user asks for comparisons/alternatives
* query includes “similar to”
* keyword search returns sparse results
* agent requests `providers=["exa"]`

## Upgrade 3: deep rerank mode

Add:

```python
web_search(..., deep_rerank=False)
```

When enabled:

1. Run normal search.
2. Take top 15 candidates.
3. Fetch first 2–4k chars from each.
4. Rerank on title + snippet + extracted excerpt.
5. Return top N.

This will improve research quality for complex comparisons.

## Upgrade 4: search evaluation harness

You already have analytics. Turn it into an eval loop.

Create a small YAML eval set:

```yaml
- id: mcp_transport_latest
  query: "latest MCP transport Streamable HTTP SSE"
  expected_domains:
    - modelcontextprotocol.io
  freshness_required: true

- id: python_package_api
  query: "FastMCP progress reporting context report_progress"
  expected_domains:
    - gofastmcp.com
  freshness_required: true

- id: exact_error
  query: '"TypeError: object NoneType can not be used in await expression"'
  rewrite_expected: false
```

Track:

| Metric             | Meaning                            |
| ------------------ | ---------------------------------- |
| MRR@10             | First good result rank             |
| nDCG@10            | Quality-weighted ranking           |
| provider coverage  | Which providers contributed        |
| freshness hit      | Whether current docs/news appeared |
| duplicate rate     | URL/domain repetition              |
| extraction success | Whether content fetch worked       |
| cost/latency       | Provider cost and wall time        |

This would give you evidence before changing weights/providers.

## Upgrade 5: split `server.py`

Suggested structure:

```text
tools/
  search.py
  content.py
  academic.py
  youtube.py
  ai_search.py
  analytics.py
resources/
  status.py
  docs.py
prompts/
  research.py
server.py
```

`server.py` should mostly instantiate FastMCP, register modules, and start the server.

---

# 12. Suggested roadmap

## First evening

1. Fix domain boost/block cache behavior.
2. Fix page cache option identity.
3. Fix Gemini env/doc mismatch.
4. Reindent/fix content pipeline root trace span.
5. Add 6–8 pytest tests around those fixes.

## First week

1. Add freshness-sensitive cache bypass/short TTL.
2. Add provider diagnostics to `web_search` response.
3. Add tool profiles: `minimal`, `core`, `research`, `full`.
4. Add adaptive routing for Tavily/Brave/Jina based on query intent.
5. Add eval YAML with 20 personal queries.

## Later

1. Native Exa provider.
2. Deep rerank mode.
3. Better provider-specific domain filters.
4. Use analytics to auto-tune provider weights.
5. Split `server.py` into smaller tool modules.

---

# 13. Final evaluation

Your MCP is already strong. The architecture shows good instincts:

* exact-search protection
* multi-provider discovery
* RRF merge
* reranking
* content extraction fallbacks
* batch/windowed content
* analytics
* provider health
* MCP resources/prompts
* progress reporting

The biggest issue is not lack of features. It is that a few **cache identity and routing details can quietly make high-level behavior wrong**. Fixing those will produce a large improvement without adding much complexity.

My top five recommendations, in order:

1. **Fix domain boost/block behavior across cache hits.**
2. **Fix page cache keying for extraction options.**
3. **Add freshness-aware cache bypass/short TTL.**
4. **Standardize provider config/docs, especially Gemini/Pollinations.**
5. **Add a small eval harness and run it after every provider/rerank/cache change.**

## Verification summary

✅ Executed locally in the sandbox:

* Read and parsed the attached `repomix-output.xml`.
* Extracted 134 Python files.
* Counted approximate LOC and module distribution.
* Ran AST parsing over all extracted `.py` files; no syntax parse failures were found.
* Inspected tool registration, resources, prompts, search orchestration, provider registry, cache behavior, rerank pipeline, and content pipeline.

⚠️ Not executed here:

* I did not run the MCP server end-to-end because several runtime dependencies and provider API keys are not present in the sandbox.
* I did not make live provider calls to SearXNG, Brave, Tavily, Gemini, Voyage, Jina, Composio, or Exa.
* The provider comparison is based on the code inspection plus current official/provider documentation.

[1]: https://modelcontextprotocol.io/specification/2025-11-25 "Specification - Model Context Protocol"
[2]: https://gofastmcp.com/servers/tools "Tools - FastMCP"
[3]: https://docs.voyageai.com/reference/reranker-api "Rerankers"
[4]: https://docs.searxng.org/dev/search_api.html "Search API - SearXNG Documentation (2026.6.2+e964708c0)"
[5]: https://brave.com/search/api/?utm_source=chatgpt.com "Brave Search API"
[6]: https://api-dashboard.search.brave.com/app/documentation/web-search/get-started "Brave Search - API"
[7]: https://docs.tavily.com/documentation/api-reference/endpoint/search "Tavily Search - Tavily Docs"
[8]: https://exa.ai/docs/reference/search "Search - Exa"
[9]: https://ai.google.dev/gemini-api/docs/google-search?utm_source=chatgpt.com "Grounding with Google Search - generateContent API"
[10]: https://jina.ai/reader/?utm_source=chatgpt.com "Reader API"
[11]: https://gofastmcp.com/servers/context "MCP Context - FastMCP"


2. # Deep review of your personal web-search MCP

## Goal, assumptions, success criteria, and risks

**Goal.** Evaluate the attached personal web-search MCP implementation as a practical search/research tool for one power user, compare it with current MCP/search-server patterns, and identify gaps, quick wins, and a staged improvement roadmap.

**Assumptions.**
- The Repomix bundle is the source of truth for the implementation reviewed.
- This is a personal tool, not a product meant for distribution.
- Security, compliance, corporate governance, multi-tenant hardening, and enterprise deployment polish are intentionally out of scope unless a point directly affects personal reliability or search quality.
- External comparisons are used to calibrate capabilities, not to argue that you should replace the project.

**Success criteria.**
- Pinpoint the architecture you already have.
- Separate “already strong” from “likely fragile.”
- Compare against the practical feature set of current MCP search servers and AI-native search APIs.
- Produce prioritized, actionable changes with small patches where useful.
- Give an evaluation plan so future changes can be judged by evidence instead of vibes.

**Main risks.**
- Repomix may omit files not included in the bundle, such as README, dependency manifests, tests, or deployment configs.
- Static analysis can identify likely issues, but runtime behavior still needs instrumented test runs.
- Some provider APIs and MCP platform conventions are moving targets as of 2026.

---

## Executive diagnosis

Your MCP is not just a wrapper around a search API. It is closer to a small personal search platform with:

- A FastMCP server and multiple model-facing tools.
- Multi-provider search orchestration.
- Query rewriting and decomposition.
- Provider selection modes.
- Weighted RRF merge, host caps, reranking, recency bonus, and MMR diversity.
- Exact and semantic query caching.
- Content fetching with specialist resolvers for GitHub, StackExchange, arXiv, Wikipedia, PDFs, HTML, Jina fallback, and browser fallback.
- Batch fetching with continuation cursors.
- YouTube, academic, Gemini, Grok, Perplexity-style, Composio, analytics, and agentic research layers.
- OpenTelemetry/Langfuse/DuckDB-style analytics plumbing.

That is a strong personal research stack. Compared with typical web-search MCP servers, the main advantage is breadth and orchestration: it can combine classical search, community sources, AI search, academic search, content extraction, and an agentic layer in one local server. Compared with commercial AI-native search APIs, the main disadvantage is that relevance quality is not yet objectively measured, and several small cache/provider-routing bugs can undermine trust in result freshness and controllability.

**Headline assessment:** the core design is ambitious and useful; the fastest improvements are not more providers, but tighter cache identity, clearer provider semantics, runtime warnings, profile presets, and a real eval harness.

---

## Repository snapshot

From the attached Repomix bundle:

| Metric | Value |
|---|---:|
| Python files detected | 134 |
| Approximate Python lines | 30,615 |
| Approximate Python characters | 1,037,785 |
| Largest file | `server.py` |
| Largest subsystems by code size | server, telemetry, scraping/browser fallback, models, settings, provider orchestration, content fetch, reranking, academic search |

Largest files inspected:

| File | Approx. lines | Role |
|---|---:|---|
| `server.py` | 2,676 | MCP tool registration, routing, caching, top-level UX |
| `telemetry.py` | 2,440 | observability events, tracing, metrics |
| `scrape/nodriver_worker.py` | 1,279 | browser-backed scraping |
| `scrape/universal_html.py` | 1,033 | HTML extraction |
| `models.py` | 544 | Pydantic response schemas |
| `settings.py` | 435 | environment-driven configuration |
| `search/__init__.py` | 545 | provider registry and provider orchestration |
| `rerank/core.py` | 423 | reranking, recency, diversity |
| `content/fetch_pipeline.py` | 456 | content resolution and extraction |
| `agent/runner.py` | 304 | agentic research loop |

No syntax errors were found by static compilation of the Python files in the Repomix bundle.

---

## What is already strong

### 1. Tool surface is broad but mostly coherent

The server exposes a practical layered toolset:

| Tool class | Tools observed | Best use |
|---|---|---|
| Search discovery | `web_search`, `quick_web_search`, `composio_similarlinks`, `composio_image_search` | Find candidate sources |
| Content extraction | `get_content`, `batch_get_content`, `discover_links` | Convert URLs into usable context |
| AI-native synthesis/search | `gemini_search`, `perplexity_search`, `grok_search` | Quick grounded answer or trend/social search |
| Video | `youtube_search`, `youtube_transcript` | Video discovery and transcript extraction |
| Academic | `academic_search` | Paper discovery |
| Agentic research | `agentic_web_research` | Multi-step research workflows |
| Analytics | `analytics_query`, `analytics_report` | Inspect usage and quality signals |

This matches MCP’s model-facing philosophy: tools expose external actions through names, metadata, schemas, and structured results. MCP’s current tools spec says tools are server-exposed capabilities that language models can discover and invoke, each identified by name and schema metadata. The spec also supports structured content and output schemas, which your code leans into with Pydantic response models.

### 2. Search architecture is more sophisticated than a simple API wrapper

`web_search` follows a mature retrieval pipeline:

1. Normalize query.
2. Check exact cache.
3. Check semantic cache.
4. Optionally classify/rewrite/decompose query.
5. Run provider searches concurrently.
6. Merge via weighted reciprocal-rank fusion.
7. Rerank if enabled.
8. Apply optional domain boost/block.
9. Store exact and semantic caches.
10. Return structured results with guidance.

That is the right general shape for an agentic search MCP. Commercial AI search systems usually succeed because they reduce the gap between search results and usable LLM context. Your MCP is trying to do this locally through provider fusion, reranking, and fetch tools instead of relying on one hosted vendor.

### 3. Good source-specific fetchers

The content pipeline has specialized handling for source types that matter in coding and research workflows:

- StackExchange
- GitHub issues
- GitHub discussions
- Wikipedia
- arXiv
- PDF extraction
- HTML extraction
- Jina Reader fallback
- browser fallback

This is important because generic web scraping often fails on pages that are highly valuable to a developer or researcher. Specialized resolvers are one of the best differentiators in your implementation.

### 4. Agent guidance is a strong UX idea

The middleware that adds `agent_guidance`, `suggested_next_tools`, and `suggested_prompts` is a smart fit for MCP. Search tools are often underused by models because the model does not know when to transition from search to content fetch to batch fetch to synthesis. Returning guidance with the result turns the MCP into a workflow coach, not just a data source.

### 5. Analytics plumbing exists

The project already has telemetry, DuckDB analytics, error taxonomy, candidate survival, cache-hit reporting, provider performance reporting, and rewrite quality reporting. That is an excellent foundation. The next step is to connect it to a small labeled benchmark so you can measure actual quality.

---

## External comparison baseline

Current public MCP and search-tooling patterns show five practical baselines:

| Baseline | What it emphasizes | Comparison to your MCP |
|---|---|---|
| MCP spec + FastMCP | Tool schemas, structured outputs, transports, context/logging/progress | Your server is aligned: FastMCP tools, context use, structured models, stdio/SSE/streamable-http support |
| Firecrawl MCP | Search, scrape, browser interaction, deep research, retries/rate limiting | Your MCP has broader provider orchestration, but Firecrawl is stronger as a packaged scrape/crawl/browser product |
| Tavily MCP/API | Search, extract, map, crawl, research | Your MCP covers search/fetch/discover/agentic research, but map/crawl are not as first-class or guided |
| Exa MCP | AI/semantic web and code search for many AI clients | Your MCP has semantic-ish query/cache/rerank, but no Exa-style neural web/code index provider by default |
| Brave MCP/API | Independent web index plus web/local/place/image/video/news/LLM context/summarization | Your MCP optionally uses Brave-style classical search but does not expose Brave’s broader result-type taxonomy |
| Perplexity MCP | Search + reasoning/synthesis through one assistant-facing tool | Your MCP has richer raw search/fetch control; Perplexity-style tools are better for fast direct answers |

Key external facts used in this comparison:

- MCP tools are designed as model-controlled server capabilities with schemas and metadata.
- The latest MCP transport guidance uses Streamable HTTP and states that it replaced older HTTP+SSE, while allowing backward compatibility by hosting both old and new endpoints.
- FastMCP automatically turns Python functions into MCP tools, validates parameters against signatures, and returns results to the LLM.
- Firecrawl’s MCP server advertises search with full page content, scraping, page interaction, deep research, retries/rate limiting, cloud/self-hosted support, and SSE.
- Tavily’s MCP server advertises search, extract, map, and crawl tools; Tavily’s API docs also list `/search`, `/extract`, `/crawl`, `/map`, and `/research`.
- Exa’s MCP connects AI assistants to web search and code search.
- Brave’s MCP advertises web, local, place, image, video, news, LLM context, and AI-powered summarization.
- Perplexity’s MCP focuses on search and reasoning capabilities inside MCP-compatible clients.

---

## Architecture evaluation

### A. MCP and tool design

**What works well**

- Tool names are mostly direct and model-readable.
- `web_search` is positioned as the default entry point.
- `get_content` and `batch_get_content` are clear follow-ups.
- Expensive tools are separated (`grok_search`, `perplexity_search`), which helps cost control and agent behavior.
- Response schemas are typed and richly described.
- Tool descriptions include guidance and parameter intent.

**Gaps**

1. **Tool surface may be cognitively heavy for an LLM.**  
   A human can understand the difference between `web_search`, `gemini_search`, `perplexity_search`, `grok_search`, `agentic_web_research`, `quick_web_search`, and Composio search variants. An LLM may overuse the shiny answer tools or underuse `batch_get_content`.

2. **Some tools overlap in purpose.**  
   This is not bad for a personal MCP, but it makes routing harder. You need explicit “use when” rules in tool descriptions and/or dynamic guidance.

3. **Transport support is good, but installability is unclear.**  
   The code supports `stdio`, `sse`, and `streamable-http`. The bundle did not show a README/pyproject/requirements file, so the personal setup may depend on tacit knowledge.

**Quick wins**

- Add a `tool_router` resource or markdown prompt that the assistant can read:
  - “Use `web_search` first unless…”
  - “Use `get_content` when URL is known…”
  - “Use `batch_get_content` for 3+ URLs…”
  - “Use `grok_search` only for X/social/current discourse…”
- Add a `profile` setting:
  - `fast`: no rewrite, no semantic cache, no rerank, cheap providers only.
  - `balanced`: current default but with bounded rewrite/rerank.
  - `deep`: rewrite + rerank + AI search + batch fetch.
  - `offline-ish`: local/free providers only.

### B. Query policy and rewrite

**What works well**

- Precision-query detection is well thought out.
- Queries with exact syntax, URLs, quoted terms, GitHub-style filters, version strings, hex errors, constants, UUIDs, IPs, function names, etc. are protected from rewrite.
- Rewrite/decomposition has fallbacks and a timeout.
- Search variants are targeted toward keyword/community/neural/all providers.

**Gaps**

1. **Rewrite can add latency before the first useful result.**  
   The rewrite pipeline uses classifier/decomposition/model-provider calls with timeouts. This is useful for deep search but expensive for “find me the doc” workflows.

2. **Rewrite policy should be more transparent in the response.**  
   The response should explicitly say:
   - rewrite bypassed and why,
   - rewrite used and variants generated,
   - decomposition used and branches searched,
   - fallback used.

3. **Precision detection can be over- or under-conservative.**  
   For personal use, false positives are acceptable, but each false positive suppresses potentially helpful rewrite. Exact docs queries like “React 19 compiler cache” may need rewrite, while “React 19” might be protected only if the version detector catches it.

**Quick wins**

- Change `rewrite: bool` into `rewrite: "auto" | "off" | "force"` while keeping backward compatibility.
- Add `rewrite_trace` to `web_search` output:
  ```json
  {
    "mode": "bypassed|expanded|fallback",
    "reason": "precision_signal|user_off|timeout|model_error|expanded",
    "variants": [
      {"query": "...", "target": "keyword", "rationale": "..."}
    ]
  }
  ```
- Add a `fast_first` option:
  - Run original query immediately.
  - Run rewrites concurrently.
  - Merge late results if they arrive before the budget.

### C. Provider registry and routing

**What works well**

- Provider modes (`always`, `conditional`, `never`) are a good personal cost-control abstraction.
- Free providers are fired concurrently.
- Paid providers are behind a semaphore.
- Request-scoped provider budget and failure demotion are good ideas.
- Community providers like GitHub, StackExchange, HN, Reddit are valuable for technical research.

**Important issue: `requires_key` appears not to be honored.**

`ProviderConfig` has `requires_key`, but `is_available()` returns false whenever `env_key` is set and the env var is missing, regardless of `requires_key`. In the registry, `stackexchange` is marked `requires_key=False` but also has `env_key="STACKEXCHANGE_APP_KEY"`. With the current availability logic, StackExchange can be treated as unavailable unless that env var is set.

**Patch sketch**

```python
def is_available(self) -> bool:
    if self.mode == ProviderMode.NEVER:
        return False

    # No primary credential required.
    if not self.requires_key:
        if self.extra_env_keys:
            return all(os.environ.get(k, "").strip() for k in self.extra_env_keys)
        return True

    # Primary credential required.
    if self.env_key and not os.environ.get(self.env_key, "").strip():
        return False

    return all(os.environ.get(k, "").strip() for k in self.extra_env_keys)
```

**Important UX issue: explicit `providers` looks like an allow-list.**

If a caller passes `providers=["github_graphql"]`, the implementation likely searches only that provider rather than “default providers plus GitHub.” That may be intentional, but model users often interpret `providers` as “include these too.” This can quietly reduce recall.

**Recommendation**

Add one of these:

```python
provider_policy: Literal["default_plus", "only", "auto"] = "auto"
```

or:

```python
include_default_providers: bool = True
```

Default for personal use should probably be `default_plus`, because it is more forgiving.

### D. Merge, rerank, and diversity

**What works well**

- Weighted RRF is a strong baseline for heterogeneous providers.
- Host caps reduce one-domain dominance.
- Provider agreement is captured via provider counts.
- Reranking has multiple stages:
  - bi-encoder filtering,
  - provider reranker,
  - recency bonus,
  - MMR diversity.

**Gaps**

1. **Quality is architected but not yet proven.**  
   The stack is sophisticated, but there is no visible labeled IR benchmark. Without one, you cannot know whether reranking, rewrite, semantic cache, or provider weighting improves actual outcomes.

2. **RRF telemetry may not be consistently enabled.**  
   The merge function has telemetry support, but the default parameter appears disabled. If merge-level quality signals are important, turn them on or remove dead-path instrumentation.

3. **Domain boost/block is applied after search and not applied to cache hits.**  
   This is a practical correctness bug. If a cached search response is returned before domain filtering, the user’s `domain_block` and `domain_boost` can be ignored. Even if you include those fields in the search identity key later, you should still apply post-filters on cached responses before return.

**Patch sketch**

```python
def _finalize_search_response(
    response: dict,
    *,
    query: str,
    domain_boost: list[str] | None,
    domain_block: list[str] | None,
) -> dict:
    response = _normalize_lightweight_search_response(response, query=query)
    if domain_boost or domain_block:
        response["results"] = _apply_domain_filters(
            response.get("results", []),
            domain_boost,
            domain_block,
        )
    return response
```

Use it for exact cache, semantic cache, and fresh search returns.

Also add `domain_boost` and `domain_block` to the cache identity key, or explicitly mark them as non-identity parameters and always apply them post-cache.

### E. Exact and semantic caching

**What works well**

- Exact cache gives deterministic speedups.
- Semantic cache can reduce repeated research cost.
- Single-flight prevents duplicate concurrent searches.

**Gaps**

1. **Cache identity is under-specified for user-visible filters.**  
   The domain-filter issue above is the biggest example.

2. **Semantic cache can return stale or over-broad results.**  
   This is fine for personal use if the response clearly says “semantic cache hit” and exposes age/score. It is risky for fresh/current topics.

3. **Freshness control should be first-class.**  
   Current queries need different caching behavior than evergreen docs.

**Quick wins**

- Add to every cached search response:
  ```json
  {
    "cache": {
      "hit": "none|exact|semantic",
      "age_seconds": 1234,
      "semantic_score": 0.91,
      "freshness_policy": "allow|bypass|stale_while_revalidate"
    }
  }
  ```
- Bypass semantic cache automatically when:
  - `time_range` is set,
  - query contains “latest/current/today/this week/2026”,
  - `research_goal` includes freshness/currentness,
  - provider list includes `grok`/current-news-specific provider.
- Add `cache_policy: "use" | "bypass" | "refresh"` parameter.

### F. Content extraction and page cache

**What works well**

- Specialist resolvers are a major advantage.
- PDF, HTML, metadata, link extraction, fallback fetchers, and browser fallback form a robust ladder.
- `char_offset`/`char_length` pagination is important for MCP context management.
- `summary_mode` and `focus_query` provide useful compression.

**Important issue: `strip_selectors` is not part of page-cache identity.**

`get_content` builds fetch options including `strip_selectors`, but the page cache lookup uses only the normalized URL. That means:
- A cached full page can be returned when the caller asks to strip selectors.
- A page cached after stripping selectors can be returned later when the caller expects unstripped content.

**Patch options**

Option 1: include extraction options in the cache key.

```python
cache_key = build_page_cache_key(
    normalized_url,
    strip_selectors=strip_selectors,
)
cached = get_page_cache().lookup(cache_key)
```

Option 2: bypass page cache when `strip_selectors` is provided.

```python
use_page_cache = not strip_selectors
```

Option 1 is better; option 2 is safer and faster to implement.

**Other improvements**

- Store `fetched_url`, `source_type`, `content_type`, and content hash in the page cache so cache hits preserve source metadata.
- Add freshness metadata: `fetched_at`, `age_seconds`, `etag`, `last_modified` where available.
- For long pages, expose heading-aware offsets:
  ```json
  {
    "sections": [
      {"title": "Installation", "offset": 2350, "length": 1420}
    ]
  }
  ```

### G. Batch fetch

**What works well**

- URL dedupe.
- Concurrency limits.
- Total character budget.
- Continuation cursor.
- Per-item content windows.

**Gaps**

1. **Dedupe should canonicalize URLs, not just compare raw strings.**
2. **The stated total timeout and actual wall-clock behavior can diverge.**  
   Per-URL timeouts are useful, but for many URLs the whole batch can exceed the intended budget if each concurrency wave spends the minimum timeout.
3. **Continuation cursor should include a content hash/version.**  
   This prevents confusing continuation if content changes between calls.

**Quick wins**

- Canonicalize before dedupe.
- Add a monotonic deadline for the whole batch.
- Return a `batch_summary`:
  ```json
  {
    "urls_requested": 12,
    "urls_fetched": 9,
    "urls_failed": 3,
    "chars_returned": 112000,
    "has_more": true,
    "next_cursor": "...",
    "failure_types": {"timeout": 2, "blocked": 1}
  }
  ```

### H. Agentic research

**What works well**

- Depth profiles (`quick`, `normal`, `deep`) are the right abstraction.
- Tool-call and timeout budgets exist.
- Final-answer tool / structured payload extraction is a good pattern.
- Knowledge graph construction from messages and source records is promising.
- External MCP tool integration is a strong extension point.

**Issue: success metrics may be recorded before execution completes.**

`agentic_web_research` appears to record a success event before the actual agent execution result is known, and also records failure in the exception path. That can inflate success metrics or double-count failures.

**Patch**

Move success recording after successful completion only:

```python
try:
    result = await run_agent(...)
    record_mcp_tool_call("agentic_web_research", success=True)
    return result
except Exception:
    record_mcp_tool_call("agentic_web_research", success=False)
    raise
```

**More important strategic gap**

The agentic tool should not just be “an agent with search tools.” It should produce an evidence pack:
- claims,
- citations,
- source snippets,
- source confidence,
- conflicts,
- missing evidence,
- recommended next searches.

That will make it more useful than raw web search for deep personal research.

### I. Analytics and evaluation

**What works well**

- Usage analytics and descriptive reports are already present.
- Provider performance, cache hits, error taxonomy, rewrite quality, fetch quality, and candidate survival are the right categories.

**Main gap: no visible gold-set eval harness.**

You need a lightweight benchmark. Not a huge academic IR suite; just a personal quality gate.

Recommended eval set:

| Segment | Example cases | Metric |
|---|---|---|
| Exact technical error | error strings, stack traces, package bugs | gold URL in top 5; MRR@10 |
| Official docs | API version docs, changelog queries | official-domain presence@5 |
| Current/fresh | “latest release”, recent incidents | freshness correctness |
| Community debugging | GitHub issues, StackExchange, Reddit/HN | useful-community-hit@10 |
| Academic | known paper title/author/topic | DOI/arXiv/paper match@10 |
| Known URL fetch | docs, blogs, PDFs | extraction success, useful chars |
| Multi-source research | broad topic | citation coverage, contradiction handling |
| YouTube | known video/topic | transcript availability and relevance |

Minimum metrics:

| Metric | Why |
|---|---|
| Success@5 | Did the right answer/source appear early? |
| MRR@10 | How far down is the first good result? |
| NDCG@10 | Are the best sources ranked higher? |
| Recall@10 by domain/source type | Are important source categories present? |
| Citation coverage | How many final claims are supported by fetched sources? |
| Extraction success rate | How often search results become usable text? |
| p50/p95 latency | Personal UX depends on responsiveness |
| Cost per successful answer | Especially for AI-native providers |
| Cache correctness failures | Domain filter/freshness mistakes |
| Rewrite win rate | Did rewrite improve or hurt? |
| Provider marginal contribution | Which provider adds unique good hits? |

Recommended tables:

```sql
create table eval_cases (
  case_id text primary key,
  segment text,
  query text,
  research_goal text,
  expected_urls text[],
  expected_domains text[],
  freshness_required boolean,
  notes text
);

create table eval_runs (
  run_id text primary key,
  run_ts timestamp,
  profile text,
  settings_json text,
  git_sha text
);

create table eval_observations (
  run_id text,
  case_id text,
  rank int,
  url text,
  domain text,
  provider text,
  score double,
  is_relevant int,
  relevance_grade int,
  extraction_status text,
  latency_ms int
);
```

---

## Feature-by-feature comparison

| Capability | Your MCP | Firecrawl MCP | Tavily MCP/API | Exa MCP | Brave MCP/API | Perplexity MCP |
|---|---|---|---|---|---|---|
| Raw web search | Strong, multi-provider | Yes | Yes | Yes | Yes | Indirect answer/search |
| Multi-provider fusion | Strong | No/limited | No/limited | No | No | No |
| Query rewrite/decomposition | Strong | Managed/opaque | Managed/opaque | Managed/semantic | No/limited | Managed/opaque |
| Full-content fetch | Strong, local pipeline | Very strong | Extract API | Some content/search outputs | Snippets/LLM context depending endpoint | Answer-oriented |
| Browser/page interaction | Browser fallback exists | Strong, explicit | Crawl/map rather than interactive browser | No | No | No |
| Map/crawl | `discover_links`, sitemap-ish | Strong | Strong first-class map/crawl | No | No | No |
| Academic search | Strong custom tool | No | Not primary | Not primary | Not primary | General web |
| YouTube/transcripts | Strong custom tools | Not primary | Not primary | Not primary | Video endpoint in Brave MCP | General web |
| Social/X trends | Grok/OpenRouter integration | Not primary | Not primary | Not primary | News/social via web | General web |
| Reranking/diversity | Strong | Managed | Managed | Semantic | Provider ranking | Managed |
| Analytics | Strong local plumbing | Product logs | Platform logs | Platform logs | API logs | Platform logs |
| Personal hackability | Very high | Medium | Medium | Low/medium | Medium | Low |
| Install/package polish | Unclear from bundle | Strong | Strong | Strong | Strong | Strong |
| Objective eval | Needs work | Vendor-managed/opaque | Vendor-managed/opaque | Vendor-managed/opaque | Vendor-managed/opaque | Vendor-managed/opaque |

**Interpretation:** your MCP wins on control, breadth, and composability. It loses on packaging polish, managed crawl/extract convenience, and measured quality.

---

## Prioritized recommendations

### P0: same-day quick wins

| Priority | Fix | Why it matters | Effort |
|---|---|---|---|
| P0 | Apply domain boost/block to cached responses and/or include them in cache key | Prevents user-visible filter violations | Small |
| P0 | Include `strip_selectors` in page-cache identity or bypass cache when used | Prevents wrong content from cache | Small |
| P0 | Honor `requires_key` in provider availability | Restores intended optional-key providers | Small |
| P0 | Clarify provider allow-list semantics | Avoids accidental recall collapse | Small |
| P0 | Move agentic success metric after actual success | Fixes misleading analytics | Small |
| P0 | Add runtime provider warnings to search response | Avoids silent “no results” when providers fail | Small/medium |

### P1: one-week improvements

| Priority | Fix | Why it matters |
|---|---|---|
| P1 | Add `profile=fast/balanced/deep/local` | Personal workflow control |
| P1 | Add `cache_policy=use/bypass/refresh` | Freshness trust |
| P1 | Add `rewrite_trace` | Debug search behavior |
| P1 | Add gold-set eval harness with 50–100 cases | Converts architecture into measurable quality |
| P1 | Add provider marginal-contribution analytics | Remove or demote low-value providers |
| P1 | Add whole-call deadlines for search and batch fetch | Predictable latency |
| P1 | Preserve source metadata on page-cache hits | Better trust and debugging |

### P2: two- to four-week roadmap

| Priority | Fix | Why it matters |
|---|---|---|
| P2 | Guided crawl/map tool | Closes gap vs Tavily/Firecrawl |
| P2 | Evidence-pack output for agentic research | Turns search into trustable synthesis |
| P2 | Exa provider or similar neural/code search | Improves semantic/code-discovery coverage |
| P2 | Result clustering by source type | Better research coverage |
| P2 | Install/dependency docs | Makes future personal setup reproducible |
| P2 | Heading-aware content navigation | Better use of long docs |
| P2 | Freshness-aware cache validators | Better current-topic behavior |

---

## Decision matrix for daily use

| Task | Use this path | Recommended settings |
|---|---|---|
| Known URL | `get_content` | `cache_policy=use`, summary as needed |
| 3+ known URLs | `batch_get_content` | canonicalize, set total char budget |
| Exact error/debugging | `web_search` → `get_content` | `rewrite=off/auto`, boost GitHub/StackExchange, include defaults |
| Official docs | `web_search` → `batch_get_content` | domain boost official docs; maybe `rewrite=auto` |
| Current event / “latest” | `gemini_search` or `web_search` with fresh providers | bypass semantic cache; use time range |
| X/social trend | `grok_search` | use only when social/current discourse matters |
| Academic paper discovery | `academic_search` | request sources explicitly if needed |
| Broad research synthesis | `agentic_web_research` | `depth=normal/deep`, require evidence pack |
| Find related pages on a site | `discover_links` | add max depth/page caps |
| Video evidence | `youtube_search` → `youtube_transcript` | use transcript before citing video |

---

## Suggested “quality profiles”

```yaml
profiles:
  fast:
    rewrite: off
    rerank: false
    semantic_cache: false
    providers: default_free
    timeout_s: 12

  balanced:
    rewrite: auto
    rerank: true
    semantic_cache: true
    providers: default_plus_conditionals_by_intent
    timeout_s: 25

  deep:
    rewrite: auto
    decomposition: true
    rerank: true
    semantic_cache: use_unless_fresh
    providers: default_plus_ai_plus_community
    fetch_top_k: 5
    timeout_s: 90

  local:
    rewrite: off
    rerank: local_only
    semantic_cache: true
    providers: searxng,ddg,hackernews,reddit,stackexchange
    timeout_s: 20
```

---

## Specific patch checklist

### Search cache finalization

- [ ] Create `_finalize_search_response()`.
- [ ] Call it for exact-cache hit.
- [ ] Call it for semantic-cache hit.
- [ ] Call it for fresh response.
- [ ] Include `domain_boost`/`domain_block` in cache identity or always post-filter cached responses.
- [ ] Add tests:
  - cached query + `domain_block=["example.com"]` excludes example.com,
  - cached query + `domain_boost=["github.com"]` boosts GitHub,
  - semantic cache hit respects same.

### Page cache extraction identity

- [ ] Add extraction options hash.
- [ ] Include at least `strip_selectors`.
- [ ] Decide whether `include_links` and `include_metadata` are cache payload fields or cache identity fields.
- [ ] Preserve `fetched_url`, `source_type`, `content_type`, `fetched_at`, and content hash.
- [ ] Add tests:
  - unstripped then stripped call,
  - stripped then unstripped call,
  - cache hit metadata preserved.

### Provider availability

- [ ] Fix `requires_key`.
- [ ] Add tests for DDG, SearXNG, StackExchange, Composio with extra env keys.
- [ ] Add a startup provider diagnostics endpoint/tool.

### Provider runtime warnings

- [ ] Return provider errors in a structured list:
  ```json
  {
    "provider": "brave",
    "stage": "search",
    "error_type": "timeout",
    "retryable": true
  }
  ```
- [ ] Distinguish “provider unavailable” from “provider failed at runtime” from “provider returned zero hits.”

### Evaluation harness

- [ ] Add `eval_cases.yaml`.
- [ ] Add `python -m kindly.eval run --profile balanced`.
- [ ] Store results in DuckDB.
- [ ] Report:
  - per-segment Success@5,
  - per-provider marginal unique hits,
  - rewrite win/loss,
  - cache hit quality,
  - p95 latency,
  - extraction success.

---

## Final verdict

Your web-search MCP is already powerful enough for serious personal research. Its strongest differentiators are multi-provider orchestration, source-specific content extraction, dynamic guidance, and local analytics. The project’s main risk is not lack of features; it is that a few small identity/routing issues can quietly return the wrong results, and the sophisticated retrieval stack is not yet backed by a stable evaluation harness.

The best next move is a quality-hardening sprint, not a feature sprint:

1. Fix cache identity and provider availability.
2. Add explicit profiles and cache/rewrite traces.
3. Add runtime warnings.
4. Build a small eval harness.
5. Only then tune reranking, add providers, or build guided crawl/research features.

If those steps are done, this becomes a very strong personal MCP: more controllable than hosted search MCPs, more transparent than answer-only tools, and more extensible than single-provider search wrappers.
