# Web Search MCP Objective Delta Report

Generated: 2026-05-12T08:19:32+02:00

## Current Baseline

This MCP already has:

- `web_search`: lightweight result discovery only, with provider selection, rewrite, exact cache, RRF/rerank path: `src/kindly_web_search_mcp_server/server.py:386`, `src/kindly_web_search_mcp_server/search/orchestrator.py:40`
- `get_content` / `batch_get_content`: explicit URL fetch, page cache, specialized resolver, per-URL failure isolation: `src/kindly_web_search_mcp_server/server.py:839`
- Rerank pipeline: bi-encoder filter, Jina rerank, MMR diversity: `src/kindly_web_search_mcp_server/rerank/core.py:29`

## Objective Deltas Found

| Finding | Source Evidence | Current Status | Concrete Adaptation |
|---|---|---|---|
| Typed search categories: text/news/images/videos/books with backend allowlists and date filters | Nymbo `Web_Search.py` defines `search_type`, backend choices, per-type backend allowlists, and date filter mapping: `C:\Users\Jan\Documents\GitHub\1Agents1\nymbo-tools-mcp-docker-qwen\Modules\Web_Search.py:17`, `C:\Users\Jan\Documents\GitHub\1Agents1\nymbo-tools-mcp-docker-qwen\Modules\Web_Search.py:37` | Partially present only via generic providers + YouTube tool | Add explicit `search_type` or separate `news_search` / `image_search`, with provider routing per type |
| Pagination/continuation in search results | Nymbo supports `page`, `offset`, computes `total_needed`, and returns pagination hints: `C:\Users\Jan\Documents\GitHub\1Agents1\nymbo-tools-mcp-docker-qwen\Modules\Web_Search.py:194` | Missing in current `web_search` | Add `page`/`offset` or `cursor` plus `next_offset` in response |
| Separate query optimization for lexical search vs AI answer search | Nymbo has `optimize_for_search_engine()` and `optimize_for_ai_search()` with Mistral -> HF -> bypass fallback: `C:\Users\Jan\Documents\GitHub\1Agents1\nymbo-tools-mcp-docker-qwen\Modules\_query_optimizer.py:1`, `C:\Users\Jan\Documents\GitHub\1Agents1\nymbo-tools-mcp-docker-qwen\Modules\_query_optimizer.py:430` | Current rewrite is mostly one search-oriented rewrite path | Split rewrite prompts/metadata by provider class: SERP, neural search, AI answer |
| Multi-instance SearXNG failover | Nymbo loads `SEARXNG_INSTANCES`, retries instances, has connection pooling: `C:\Users\Jan\Documents\GitHub\1Agents1\nymbo-tools-mcp-docker-qwen\Modules\_searxng_client.py:89`, `C:\Users\Jan\Documents\GitHub\1Agents1\nymbo-tools-mcp-docker-qwen\Modules\_searxng_client.py:142` | Current repo appears single-base-url oriented | Add optional `SEARXNG_BASE_URLS` / `SEARXNG_INSTANCES` with chosen-instance diagnostics |
| Search plus scrape in one API call | Firecrawl `/search` accepts `scrapeOptions`; SDK treats results with `markdown/html/rawHtml/links` as documents: `https://github.com/firecrawl/firecrawl/blob/main/apps/python-sdk/firecrawl/v2/methods/search.py#L10`, `https://github.com/firecrawl/firecrawl/blob/main/apps/python-sdk/firecrawl/v2/methods/search.py#L52` | Current `web_search` does not fetch content | Add explicit `search_and_fetch` or `web_search(include_content=true, fetch_top_k=N)` |
| Search scoping categories and invalid URL filtering | Firecrawl `SearchRequest` includes `sources`, `categories`, include/exclude domains, `tbs`, location, `ignore_invalid_urls`, `scrape_options`: `https://github.com/firecrawl/firecrawl/blob/main/apps/python-sdk/firecrawl/v2/types.py#L1214` | Current providers list is coarse; no GitHub/PDF/research category parameter | Add `categories=["github","pdf","research"]`, `include_domains`, `exclude_domains`, freshness/location filters |
| Map/crawl/extract/search as separate exposed tool surfaces | Tavily MCP implementation exposes `tavily_search`, `tavily_extract`, `tavily_crawl`, `tavily_map`: `https://github.com/Klavis-AI/klavis/blob/main/mcp_servers/tavily/server.py#L80`, `https://github.com/Klavis-AI/klavis/blob/main/mcp_servers/tavily/server.py#L156` | Current repo has search + fetch, no map/crawl | Add `map_site` and bounded `crawl_site`; Tavily-style schemas already show `max_depth`, `max_breadth`, `select_paths`, `allow_external` |
| Rich content retrieval options: text/highlights/summary/context/livecrawl/subpages | Exa tool exposes `type=auto/instant/fast/deep`, categories, text, highlights, summary, livecrawl, subpages: `https://github.com/strands-agents/tools/blob/main/src/strands_tools/exa.py#L205` | Current `get_content` returns whole markdown, no targeted highlights/summary | Add `get_content(mode="full|highlights|summary", query=..., max_chars=...)` or `extract_content` |
| Fetch output profiles | `materials/Fetch/app.py` supports metadata, text, links, verbosity, max chars, max links: `C:\Users\Jan\Documents\GitHub\1Agents1\materials\Fetch\app.py:188`, `C:\Users\Jan\Documents\GitHub\1Agents1\materials\Fetch\app.py:247` | Current `get_content` has fixed output shape | Add optional output controls to `get_content` |
| Broader extraction chain | CrewAI material tries HEAD PDF branch, PyMuPDF, newspaper, MarkItDown, trafilatura, readability, BS4, Playwright: `C:\Users\Jan\Documents\GitHub\1Agents1\materials\crewai-multiagent-research\app.py:396`, `C:\Users\Jan\Documents\GitHub\1Agents1\materials\crewai-multiagent-research\app.py:439` | Current resolver has strong specialized handlers, but fewer generic article/document extractors | Add generic PDF content-type handling and optional MarkItDown/newspaper stage |
| Adaptive query exploration | Local deep research tracks strategy attempts/candidates/quality, generates direct/synonym/category/related/constraint queries, adapts strategy: `C:\Users\Jan\Documents\GitHub\1Agents1\materials\local-deep-research-main\local-deep-research-main\src\local_deep_research\advanced_search_system\candidate_exploration\adaptive_explorer.py:60`, `C:\Users\Jan\Documents\GitHub\1Agents1\materials\local-deep-research-main\local-deep-research-main\src\local_deep_research\advanced_search_system\candidate_exploration\adaptive_explorer.py:89`, `C:\Users\Jan\Documents\GitHub\1Agents1\materials\local-deep-research-main\local-deep-research-main\src\local_deep_research\advanced_search_system\candidate_exploration\adaptive_explorer.py:185` | Current rewrite generates variants once; no result-feedback loop | Add adaptive loop for a deeper search mode: evaluate result count/diversity/quality, issue follow-up query variants |
| Search-and-extract endpoint pattern | Webscout exposes `/api/search-and-extract` and `adv_web_search` search -> fetch top results -> prompt model: `C:\Users\Jan\Documents\GitHub\1Agents1\materials\Webscout-API\app.py:300`, `C:\Users\Jan\Documents\GitHub\1Agents1\materials\Webscout-API\app.py:398` | Current agent must chain search then batch fetch manually | Add one bundled search+fetch endpoint if desired |
| Scraper backend registry | GPT Researcher selects scraper backend from PDF/arxiv/BS/browser/nodriver/tavily_extract/firecrawl and dedupes URLs: `C:\Users\Jan\Documents\GitHub\1Agents1\materials\gpt_researcher\scraper\scraper.py:31`, `C:\Users\Jan\Documents\GitHub\1Agents1\materials\gpt_researcher\scraper\scraper.py:137` | Current resolver is staged but not configurable by scraper backend | Add `extractor="auto|http|browser|tavily|firecrawl|markitdown"` or internal registry |
| External MCP wrapping pattern | Nymbo BrightData wrapper validates JSON with Pydantic, wraps upstream MCP result in `tool/arguments/isError/content/structuredContent/error`, captures semantic cache: `C:\Users\Jan\Documents\GitHub\1Agents1\nymbo-tools-mcp-docker-qwen\Modules\_brightdata_mcp.py:15`, `C:\Users\Jan\Documents\GitHub\1Agents1\nymbo-tools-mcp-docker-qwen\Modules\_brightdata_mcp.py:54`, `C:\Users\Jan\Documents\GitHub\1Agents1\nymbo-tools-mcp-docker-qwen\Modules\_brightdata_mcp.py:200` | Current provider adapters vary by provider | Use this envelope for any Firecrawl/BrightData/Tavily external wrapper |
| BrightData base tool set | BrightData README lists base tools: `search_engine`, `search_engine_batch`, `scrape_as_markdown`, `scrape_batch`, `discover`: `https://github.com/brightdata/brightdata-mcp/blob/main/README.md#L215` | Not present except Composio-like integrations | Add BrightData wrappers if premium/proxy-backed extraction is wanted |

## Non-Deltas

These are not new because current repo already does them:

- Multi-provider fanout + merge: already in `src/kindly_web_search_mcp_server/search/orchestrator.py:40`
- RRF/rerank/diversity: already in current search/rerank path
- Batch URL fetch: already in `src/kindly_web_search_mcp_server/server.py:839`
- AI synthesized search: already has `gemini_search` and `perplexity_search`

## Actual Adaptation Targets

1. Add `search_and_fetch` / `deep_search` that combines search, adaptive follow-up, rerank, and top-k extraction.
2. Add typed search controls: `search_type`, `category`, `include_domains`, `exclude_domains`, date/location filters.
3. Add `map_site` and `crawl_site` using Tavily/Firecrawl/Crawl4AI/trafilatura patterns.
4. Add fetch profiles to `get_content`: metadata/links/text/highlights/summary/max chars.
5. Add configurable extractor registry and optional external extractors: Tavily Extract, Firecrawl Scrape, BrightData scrape.
6. Add SearXNG multi-instance failover and diagnostics.
7. Split query optimization by target: SERP vs neural search vs AI answer.
