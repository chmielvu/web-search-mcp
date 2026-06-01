# kindly-web-search-mcp — External Research Synthesis
## 35 repos analyzed — 23 actionable improvement recommendations

### Current Architecture Strengths (well ahead of ecosystem)
- Multi-provider RRF fusion (k=60) — no other repo does this
- 3-stage rerank pipeline — bi-encoder → Jina cross-encoder → MMR diversity
- Semantic cache (LanceDB) — unique in the ecosystem
- Staged content resolver (StackExchange/GitHub/arXiv/Wikipedia/HTTP/browser) — unique breadth
- MCP-native FastMCP with OpenTelemetry — modern, instrumented
- Differentiated rate limiting + SingleFlight coalescing
- Query rewrite with Mistral — provider-aware keyword/neural paths
- YouTube transcript+search, Gemini grounding, Perplexity tools

---

## HIGH IMPACT (implement these)

### 1. Provider Health/Cooldown System
**Source:** hermes-web-search-plus, MegaMemory
- Track failures per provider with exponential backoff cooldown
- Persistent state file (provider_health.json)
- Gate every provider call: skip cooldowned providers
- Auto-reset on first successful call after cooldown
- **Current gap:** No persistent health tracking, no cooldown gating — dead provider calls waste time

### 2. Intent-Based Provider Routing
**Source:** hermes-web-search-plus QueryAnalyzer, web-search-plus regex router, perplexideez
- Regex-based intent detection with weighted signals
- Route to optimal provider subset per query intent (shopping→Serper, research→Tavily, code→SearXNG)
- "not_needed" detection to skip search for greetings/basic chat
- **Current gap:** All providers fire always; routing only determines rewrite strategy

### 3. Partial Results + Cancellation Tools
**Source:** AgentWebSearch-MCP, MindSearch
- `get_search_status(job_id)` — real-time partial results
- `cancel_search(job_id)` — abort long-running deep searches
- Stream partial results as they arrive from providers
- **Current gap:** All-or-nothing search with no visibility into progress

### 4. Content Quality / Junk Detection
**Source:** hyperresearch looks_like_junk()
- Cloudflare bot detection ("verify you are human")
- Login wall detection (redirected-to-login)
- Error page detection (404/403/500)
- Cookie consent boilerplate detection
- High non-printable character ratio detection
- **Current gap:** status="blocked" but no content-level quality inspection

### 5. Deep Research / Recursive Search Mode
**Source:** deep-research-web-ui, RecurSearch, Flash-Searcher
- Breadth × depth tree exploration
- Per-node pipeline: generate_queries → search → extract_learnings → follow_up_questions
- URL deduplication across tree branches
- Report generation with numbered citations across nodes
- Failed-node retry without restarting entire tree
- **Current gap:** No recursive or deepening search capability; one-shot only

### 6. Golden Query Evaluator (Regression Test Harness)
**Source:** hermes-web-search-plus golden_eval.py, Mind2Web-2
- Fixed 8-12 queries spanning diverse domains
- Capture: latency, domain_diversity, extraction_quality, error_flags
- JSONL + markdown report output
- Run before every release as CI gate
- **Current gap:** No structured evaluation harness — hard to detect regressions

---

## MEDIUM IMPACT (consider these)

### 7. Quality Report Diagnostics
**Source:** hermes-web-search-plus
- Return structured diagnostics: routing_reason, confidence, domain_diversity, thin_snippet_count, extract_recommended
- Optional `quality_report=True` param on web_search
- **Current gap:** Limited diagnostic output

### 8. Site-Scoped Query Syntax
**Source:** statespace llms.txt search
- `site: query` pattern as first-class feature
- llms.txt pre-parsed documentation index for popular frameworks
- **Current gap:** Relies on providers for site: syntax

### 9. Structured Extraction Format
**Source:** Research-Agent (Alibaba winner), open-extract
- Per-page JSON output: {rational, evidence, summary}
- Extract with smaller LLM (not the main answering model)
- Citation-ready structured output
- **Current gap:** Only raw markdown page content

### 10. ApiKeyPool with Rotation
**Source:** deep-research-web-ui
- Comma-separated API keys per provider, round-robin rotation
- Error counting with auto-disable after N consecutive failures
- File-based state persistence (.cache/keypool_*.json)
- **Current gap:** Single API key per provider — no failover

### 11. Zero-API-Key Search Fallback
**Source:** open-webSearch, one-search-mcp, AgentWebSearch-MCP
- Direct HTML scraping of Bing/DuckDuckGo using Cheerio/Playwright
- Multi-strategy CSS selector result extraction
- Browser-based fallback for JS-heavy search pages
- **Current gap:** Requires at least one API key to function at all

### 12. Semantic Exit Codes
**Source:** search-cli (Rust)
- 0=success, 1=runtime_error, 2=config_error, 3=auth_missing, 4=rate_limited
- Agents can programmatically decide retry/backoff/fix based on exit code
- **Current gap:** Standard Python exceptions only

### 13. Provider Factory Pattern
**Source:** websearch-mcp-server, search-cli
- Abstract factory for provider instantiation: `ProviderFactory.get("brave")`
- Clean interface: every provider has `search(query, num_results) → [WebSearchResult]`
- Enum-based provider registry
- **Current gap:** Providers are module-level functions, harder to extend/test

---

## LOWER PRIORITY (future roadmap)

### 14. Persistent Knowledge Graph
**Source:** MegaMemory, hyperresearch vault
- SQLite-indexed concepts across sessions (LLM-as-indexer)
- Two-way merge engine with conflict resolution
- Timeline audit table: every tool call logged
- Wiki-link graph with backlinks, hubs, provenance chains
- **Current gap:** Cache is ephemeral; no cross-session knowledge accumulation

### 15. Image Search as First-Class MCP Tool
**Source:** SurfAgent, local-llm-searxng-agent
- Dedicated image_search MCP tool
- SearXNG image engine or Composio image search
- Return image URLs + metadata
- **Current gap:** No image search capability (only web text)

### 16. Speed/Balanced/Quality Tiered Reranking
**Source:** perplexideez mode system
- Speed mode: skip cross-encoder, just bi-encoder + MMR
- Balanced mode: bi-encoder + cross-encoder, skip MMR
- Quality mode: full 3-stage pipeline (current default)
- **Current gap:** Always runs full 3-stage pipeline regardless of urgency

### 17. Adaptive Research Depth
**Source:** SurfAgent
- Dynamically adjust num_results based on query complexity
- Simple queries → 3 results, complex research → 10 results
- **Current gap:** Fixed num_results default (5), agent must guess

### 18. Keyword-Gated Search Trigger
**Source:** local-llm-searxng-agent
- Configurable keyword list: only search when query contains trigger words
- Non-trigger queries pass directly to LLM (zero search overhead)
- **Current gap:** No search gating; every call potentially fires search

### 19. Post-Generation Enrichment
**Source:** perplexideez
- After main answer: async generate follow-up questions, title, emoji
- Image/video search for source enrichment
- OpenGraph + favicon fetching for source cards
- **Current gap:** Output is raw text only

### 20. Source Reliability Scoring
**Source:** SurfAgent, RecurSearch
- Track source reliability across sessions
- Domain-level trust scoring
- Credibility evaluation as part of search pipeline
- **Current gap:** No reliability or trust modeling

### 21. DAG-Based Parallel Task Decomposition
**Source:** Flash-Searcher, MindSearch WebSearchGraph
- Represent complex queries as DAG of sub-questions
- Parallel execution of independent subtasks
- Dynamic plan optimization mid-run
- **Current gap:** Sequential ReAct loop; query rewrite handles simple expansion only

### 22. Self-Correcting Agent Loop
**Source:** agentic-rag-react
- Autonomous tool switching on low-relevance results
- Priority-ordered tool strategy: vector_search → file_search → file_read → web_search
- **Current gap:** Fixed pipeline, no runtime self-correction

### 23. LLM Output Validation / Guardrails
**Source:** Research-Agent (Alibaba winner)
- Dynamic prompt injection mid-ReAct-loop (5 guardrail types)
- Early answer interception when answer found quickly
- Content safety auto-retry with progressive escalation
- Think-tag reminders, no-action nudges
- **Current gap:** No runtime behavior correction; relies on static prompts only

---

## Cross-Cutting Patterns Worth Stealing

| Pattern | Source | Application |
|---------|--------|-------------|
| Abort-aware resource management with cleanupPromise | one-search-mcp | Browser pool, HTTP connections |
| Composite AbortSignal (caller signal + timeout) | one-search-mcp | Any async operation |
| insertMarkersByUtf8Index() for citation markers | opencode-websearch-cited | Gemini/perplexity output formatting |
| XML-tag output parsers (no JSON mode needed) | perplexideez | Ollama/local model integration |
| Tool-locked [Read, Edit] pipeline separation | hyperresearch | Content processing safety |
| runInTransaction with reentrant-safe depth tracking | MegaMemory | Cache operations, DB writes |
| Dynamic concurrency expansion (concurrency++ in recursive calls) | deep-research-web-ui | Recursive/deep search mode |
| Encounter-order tiebreaking in merge | kindly baseline (already good) | Keep — this is a strong pattern |
| subprocess curl fallback for SearXNG compatibility | ask-search | HTTP backend diversity |
| FTS5 with BM25 + highlight snippets | hyperresearch | Content search over cached pages |
| Browser auto-detection (finder.ts checks known paths) | one-search-mcp | Universal HTML browser launch |
| Turndown + GFM for HTML→Markdown | one-search-mcp | Page content extraction |
| Soft-delete with removed_at + removed_reason | MegaMemory | Cache eviction, content versioning |
