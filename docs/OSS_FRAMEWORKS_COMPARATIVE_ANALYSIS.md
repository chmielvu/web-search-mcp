# Comprehensive Comparative Analysis: Self-Hosted OSS Frameworks for Web Search, Crawling, and Content Fetching

**Date:** 2026-06-02
**Scope:** Self-hosted, open-source frameworks only. Cloud-only/SaaS solutions noted but not primary focus.

---

## Category A: Self-hosted Crawl/Fetch Frameworks

### 1. Crawl4AI (github.com/unclecode/crawl4ai)

| Metric | Value |
|---|---|
| **Stars** | ~67,600 |
| **Forks** | ~6,900 |
| **License** | Apache 2.0 |
| **Language** | Python (with Playwright for browser) |
| **Commits** | 1,528+ |
| **First Release** | 2023 |
| **Latest Version** | v0.8.7 (2026) |

#### Architecture Overview
- **Core:** Async-first Python using Playwright for headless browser control (Chromium, Firefox, WebKit)
- **Two modes:** Python library (`AsyncWebCrawler`) + Docker REST API (FastAPI on port 11235)
- **Browser pooling:** 3-tier architecture (permanent/hot/cold) with automatic promotion and cleanup
- **Crawling strategies:** BFS, DFS, BestFirst deep crawling with crash recovery (`resume_state`, `on_state_change` callbacks)
- **Prefetch mode:** 5-10x faster URL discovery by skipping markdown/content extraction
- **Caching:** SQLite-based cache with `CacheMode` (ENABLED/BYPASS/DISABLED)

#### Key Capabilities
- **JS Rendering:** Full Playwright browser integration with JS execution, dynamic content waiting, lazy-load handling, infinite scroll simulation, shadow DOM flattening
- **Anti-bot/Stealth:** v0.8.5 introduced 3-tier anti-bot detection (known vendors, generic block indicators, structural integrity checks) with automatic proxy escalation and retry chain
- **Content Extraction:**
  - Clean Markdown generation with BM25-based filtering (`PruningContentFilter`, `BM25ContentFilter`)
  - LLM-driven structured extraction (supports any LiteLLM-compatible provider: OpenAI, Ollama, etc.)
  - CSS/XPath-based extraction (`JsonCssExtractionStrategy`) without LLMs
  - Cosine similarity chunking, regex/sentence-level chunking strategies
  - PDF extraction (via pypdf)
- **Media:** Image extraction with `srcset`/`picture` support, screenshots, iframe content extraction
- **Session Management:** Persistent browser profiles, cookie preservation, proxy authentication
- **Link Analysis:** Internal/external links, citation references, numbered reference lists

#### API Design
- **Python SDK:** `AsyncWebCrawler` context manager with `arun(url, config)`
- **CLI:** `crwl` command with `--deep-crawl`, `--max-pages`, `-q` (LLM extraction), `-o` (output format)
- **Docker REST API:** FastAPI endpoints (`/crawl`, `/task/{id}`, `/monitor/*`) with JWT auth
- **Hooks System:** 8 pipeline hook points, function-based hooks API with automatic serialization
- **MCP Integration:** Direct MCP server for Claude Code, Cursor, etc.

#### Resource Requirements
- **Python:** 3.10+
- **Browser:** Chromium/Firefox/WebKit (auto-installed via `crawl4ai-setup`)
- **Docker:** `ghcr.io/unclecode/crawl4ai:latest`, `--shm-size=1g` recommended
- **RAM:** ~512MB-1GB minimum (browser overhead), 2-4GB recommended for concurrent crawling
- **CPU:** Multi-core recommended for concurrent browser pools

#### Self-hosting Complexity: **Easy-Medium**
- Docker: `docker pull unclecode/crawl4ai:latest && docker run -p 11235:11235`
- Python: `pip install crawl4ai && crawl4ai-setup`
- Real-time monitoring dashboard at `:11235/dashboard`
- Playground at `:11235/playground`

#### Community Health
- **Active development:** Very active, frequent releases (v0.8.7 as of 2026)
- **Issues/PRs:** 17 open issues, 68 open PRs — well-maintained
- **Discord:** Active community server
- **Security:** Serious about security (recent fixes: RCE via AST sandbox, SSRF, auth bypass, hardcoded JWT)

#### Strengths
- #1 most-starred crawler on GitHub by far
- Extremely LLM-friendly output with citation support
- Comprehensive anti-bot features in recent versions
- Both library and API modes
- Docker with monitoring dashboard
- Deep crawl crash recovery (unique feature)
- Multiple extraction strategies (CSS, LLM, BM25, cosine similarity)

#### Weaknesses
- Python-only (no Rust/Go/JS native SDK)
- Relatively new (2023), still maturing API surface
- Docker API had security vulnerabilities (being actively patched)
- No built-in search capability (crawl+fetch only)
- Requires browser for JS-heavy sites (resource overhead)

#### Best For
- AI/RAG pipelines needing LLM-ready markdown
- Structured data extraction from complex websites
- Both ad-hoc single-page fetching and deep site crawling
- Teams wanting monitoring dashboard + API + library flexibility

---

### 2. Spider (spider-rs/spider) — formerly spider.cloud

> **Note:** "fastCRW" does not appear to exist as a distinct major project. The dominant Rust-based web crawler in the OSS space is **Spider** by spider-rs. If fastCRW exists, it is likely a minor/niche project without significant traction.

| Metric | Value |
|---|---|
| **Stars** | ~2,500 |
| **Forks** | ~206 |
| **License** | MIT |
| **Language** | Rust (100%) |
| **Commits** | 2,232+ |
| **Latest Version** | v2.48.13 (Mar 2026) |
| **Releases** | 166 |

#### Architecture Overview
- **Core:** Concurrency-first Rust engine with async/tokio runtime
- **HTTP-first approach:** Crawls via HTTP primarily, only launches headless Chrome when JS is needed (`crawl_smart()`)
- **Streaming:** Pages stream immediately as fetched (not batched at end)
- **Cloud integration:** Same code, config change toggles Spider Cloud managed service
- **Modular crates:** `spider` (core), `spider_cli`, `spider_worker`, `spider_mcp`, `spider_agent`

#### Key Capabilities
- **JS Rendering:** Optional Chrome integration (`features = ["chrome"]`), smart mode auto-detects JS need
- **Anti-bot/Stealth:** Built-in stealth mode, proxy rotation, user-agent rotation, retry logic. Cloud tier adds residential proxies + unblocker
- **Content Extraction:** Markdown, JSON, raw HTML, WARC output formats
- **Performance:** Low-latency, high-concurrency Rust design. Claims "fastest" in class
- **Concurrency:** Configurable limit, async streaming, delay/politeness controls
- **Deep Crawling:** Depth control, subdomain support, robots.txt respect
- **Page Types:** HTML, PDF, images

#### API Design
- **Rust Crate:** `cargo add spider` — primary API
- **Node.js:** `npm i @spider-rs/spider-rs`
- **Python:** `pip install spider_rs`
- **CLI:** `cargo install spider_cli`
- **MCP Server:** `cargo install spider_mcp`
- **Cloud API:** Spider Cloud with `spider_cloud` feature

#### Resource Requirements
- **Rust toolchain:** cargo + Rust
- **Chrome:** Optional, for JS-heavy sites
- **RAM:** Minimal (Rust binary), ~128-512MB depending on concurrency
- **CPU:** Efficient, scales with concurrent requests

#### Self-hosting Complexity: **Easy-Medium**
- Rust lib: `cargo add spider`, write a few lines
- CLI: `cargo install spider_cli`
- Docker images available (ghcr.io)
- Cloud option for managed scale

#### Community Health
- **Active:** 166 releases, regular updates
- **Low open issues:** 0 open issues, well-maintained
- **Discord:** Active community
- **Documentation:** Good docs.rs + guides + 50+ runnable examples

#### Strengths
- Blazingly fast (Rust native)
- HTTP-first design minimizes resource usage
- Smart mode only launches browser when needed
- MIT license (business-friendly)
- Multi-language SDKs (Rust, Python, Node, CLI, MCP)
- Optional cloud tier for scaling

#### Weaknesses
- Less LLM-focused output vs Crawl4AI
- Smaller community than Crawl4AI/Firecrawl
- No built-in search capability
- Cloud-first monetization model (OSS is gateway)
- JS rendering optional/capped in OSS

#### Best For
- High-performance crawling at scale
- Rust-first projects needing native integration
- Teams wanting OSS local dev + cloud production option
- Low-resource environments

---

### 3. Firecrawl (github.com/firecrawl/firecrawl)

| Metric | Value |
|---|---|
| **Stars** | ~127,000 |
| **Forks** | ~7,600 |
| **License** | AGPL-3.0 (core), MIT (SDKs) |
| **Language** | TypeScript (66.8%), Python (16.8%), Rust (5%) |
| **Commits** | 5,571+ |
| **Latest Version** | v2.10 (May 2026) |

#### Architecture Overview
- **Core:** Full-stack web crawling + scraping platform with REST API
- **Components:** API server + worker queue + browser pool + search infrastructure
- **Hybrid stack:** TypeScript/Node.js API layer, Python SDK, Rust components for performance
- **Self-hostable:** Full `docker-compose.yaml` with all services

#### Key Capabilities
- **Search:** Built-in web search with full page content from results
- **Scrape:** Single URL → markdown, HTML, screenshots, structured JSON
- **Crawl:** Full site crawling with async job polling
- **Map:** URL discovery without extraction
- **Batch Scrape:** Thousands of URLs asynchronously
- **Agent:** AI-powered autonomous data gathering (describe what you need)
- **Interact:** Click, scroll, write, wait actions before extraction
- **JS Rendering:** Headless browser for JS-heavy pages
- **Anti-bot:** Rotating proxies, orchestration, rate limits, stealth (cloud tier)
- **Media:** PDF/DOCX parsing from web-hosted files
- **LLM Extraction:** Structured JSON via schema definitions, AI-powered

#### API Design
- **REST API:** Full-featured at `api.firecrawl.dev/v2/`
- **SDKs:** Python, Node.js, Java, Elixir, Rust, Go (community)
- **CLI:** `npx firecrawl-cli`
- **MCP:** Native MCP server for AI agents
- **Skills:** One-command agent integration

#### Resource Requirements
- **Docker Compose:** Multi-service deployment (Redis, workers, API, browser pool)
- **RAM:** 4-8GB recommended (full self-hosted stack)
- **CPU:** Multi-core for concurrent browser workers
- **Disk:** Database, queue storage

#### Self-hosting Complexity: **Medium-Hard**
- Full docker-compose with multiple services
- Requires API key setup, Redis, worker configuration
- Cloud version adds significant features (anti-bot, proxies, higher rate limits)
- OSS is intentionally limited vs cloud offering

#### Community Health
- **Massive:** 127k stars, largest in category
- **Active:** 76 issues, 294 PRs — very active development
- **Professional:** Well-funded (YC-backed), dedicated team

#### Strengths
- #1 most popular web data API project
- Complete platform: search + scrape + crawl + agent
- Best SDK coverage (6+ languages)
- AI Agent mode (describe what you need, get data)
- Interact mode for SPA/form-heavy sites
- Professional docs, benchmarks, reliability

#### Weaknesses
- AGPL-3.0 license (restrictive for commercial use)
- Self-hosted version intentionally gimped vs cloud
- Heavy resource requirements (full docker stack)
- Cloud-first monetization biases development
- Complex setup vs single-binary tools

#### Best For
- Full web data platform needs (search + scrape + crawl)
- Teams already using Firecrawl cloud wanting self-hosted option
- AI agent integrations needing web context
- Enterprise with resources for full deployment

---

### 4. Scrapy (scrapy/scrapy)

| Metric | Value |
|---|---|
| **Stars** | ~62,100 |
| **Forks** | ~11,600 |
| **License** | BSD-3-Clause |
| **Language** | Python (99.5%) |
| **Commits** | 11,117+ |
| **Latest Version** | 2.16.0 (May 2026) |
| **First Release** | 2008 |

#### Architecture Overview
- **Core:** Mature async Python framework (Twisted-based, migrating to asyncio)
- **Components:** Engine + Scheduler + Downloader + Spiders + Item Pipeline
- **Middleware:** Extensive middleware system for request/response processing
- **Extensible:** Plugins for JS rendering (scrapy-playwright, scrapy-splash), proxy rotation, auto-throttle
- **Production:** Built-in Telnet console, stats collection, logging, signals

#### Key Capabilities
- **JS Rendering:** Via plugins — `scrapy-playwright` (recommended) or `scrapy-splash` (legacy)
- **Anti-bot:** Via middleware — scrapy-rotating-proxies, scrapy-fake-useragent, custom middleware
- **Content Extraction:** CSS/XPath selectors, Item Loaders, structured item pipelines
- **Crawling:** Link extraction, crawl rules, sitemap following, depth limits
- **Politeness:** Built-in AutoThrottle, download delays, concurrent request limits, robots.txt
- **Export:** JSON, CSV, XML, JSON Lines, custom exporters
- **Deployment:** Scrapyd (daemon), Scrapy Cloud (Zyte), Kubernetes, Docker

#### API Design
- **Framework:** Define Spider classes with `parse()` callbacks
- **CLI:** `scrapy crawl <spider>`, `scrapy shell <url>`, `scrapy genspider`
- **Scripting:** `CrawlerProcess` / `CrawlerRunner` for embedded use
- **No REST API:** Framework only, needs wrapping (Scrapyd, custom server)

#### Resource Requirements
- **Python:** 3.10+
- **RAM:** ~256MB-1GB depending on spider complexity
- **CPU:** Single core sufficient for moderate crawling
- **Browser:** Optional, via Playwright plugin (+~500MB RAM)

#### Self-hosting Complexity: **Medium**
- Python ecosystem: `pip install scrapy scrapy-playwright`
- Requires writing spider code (no zero-code UI)
- Scrapyd for production deployment
- Extensive docs, 17+ years of community knowledge

#### Community Health
- **Massive:** 62k stars, 17+ years of development
- **Active:** 437 open issues, 191 PRs — very active
- **Professional:** Maintained by Zyte (formerly Scrapinghub)
- **Ecosystem:** 1000+ extensions, middlewares, plugins

#### Strengths
- Battle-tested for 17+ years
- Most mature and stable crawling framework
- Unmatched extensibility (middleware pipeline)
- BSD license (most permissive)
- Largest plugin ecosystem
- Excellent documentation and community resources

#### Weaknesses
- No built-in JS rendering (needs plugins)
- No LLM extraction (needs custom code)
- No REST API out of the box
- Steeper learning curve (async/Twisted, spider patterns)
- Not "modern AI-first" design
- Slower than Rust/async-native tools for pure throughput

#### Best For
- Large-scale structured crawling projects
- Teams needing maximum customization/extensibility
- Production scraping with enterprise requirements
- When reliability and maturity matter over novelty

---

### 5. Jina Reader (jina-ai/reader)

| Metric | Value |
|---|---|
| **Stars** | ~11,000 |
| **Forks** | ~820 |
| **License** | Apache 2.0 |
| **Language** | TypeScript (97.8%) |
| **Commits** | 550+ |
| **First Release** | April 2024 |

#### Architecture Overview
- **Core:** Node.js/TypeScript service converting URLs to LLM-friendly markdown
- **Two endpoints:** `r.jina.ai` (read URL), `s.jina.ai` (search + read)
- **Engine selection:** Auto-selects between headless Chrome (Puppeteer) and lightweight curl (`curl-impersonate`)
- **Stateless or cached:** S3-compatible bucket caching (MinIO) or fully stateless
- **Production:** Originally Firebase → now Cloud Run + MongoDB Atlas (SaaS stripped from OSS)

#### Key Capabilities
- **Read:** Any URL → clean markdown with `https://r.jina.ai/https://your.url`
- **Search:** Web search → top 5 results → each auto-fetched → combined markdown
- **JS Rendering:** Headless Chrome via Puppeteer with configurable timing strategies
- **PDF Support:** PDF.js parsing for PDF URLs
- **MS Office:** Word, Excel, PowerPoint via LibreOffice conversion
- **Image Captioning:** VLM-powered alt text generation for images
- **Anti-bot:** `curl-impersonate`, proxy routing, user-agent rotation
- **Content Control:** Extensive headers for output format, timing, token budgets, selectors

#### API Design
- **URL Prefix:** `https://r.jina.ai/<url>` — simplest API in existence
- **Headers:** 30+ control headers (`x-respond-with`, `x-engine`, `x-timeout`, `x-target-selector`, etc.)
- **Output:** Markdown (default), HTML, text, screenshots, JSON, frontmatter
- **Search:** `https://s.jina.ai/<query>` with optional `site=` parameter

#### Resource Requirements
- **Node.js:** 18+
- **Chrome:** Bundled in Docker image
- **LibreOffice:** For Office docs (bundled in Docker)
- **Docker:** `ghcr.io/jina-ai/reader:oss` (~1-2GB image)
- **RAM:** ~1-2GB (Chrome + Node.js)
- **Storage:** Optional S3/MinIO for caching

#### Self-hosting Complexity: **Easy**
- Docker: `docker run -p 3000:8081 ghcr.io/jina-ai/reader:oss`
- Stateless by default, caching is optional
- Prebuilt image with all dependencies

#### Community Health
- **Active:** 11k stars, Jina AI-funded
- **Open Source:** Apache 2.0, regularly synced with SaaS branch
- **Maintained:** Active development, frequent blog posts and updates

#### Strengths
- Dead simple API (prepend URL)
- Search + read in one call
- Excellent content extraction quality
- Extensive header control for power users
- PDF/Office/image support
- Apache 2.0 license
- Trivial Docker deployment

#### Weaknesses
- No crawling/sitemap support
- Rate-limited on free tier (OSS self-hosted has no limits)
- No structured JSON extraction
- Search uses top 5 only
- Single-shot URL fetch, not a crawl framework
- SaaS code stripped (MongoDB storage not in OSS)

#### Best For
- Simple URL-to-markdown conversion
- RAG pipelines needing clean page content
- AI agents needing web context with minimal integration
- Quick self-hosted reader proxy

---

## Category B: Self-hosted Search Engines

### 6. SearXNG (searxng/searxng)

| Metric | Value |
|---|---|
| **Stars** | ~31,200 |
| **Forks** | ~3,000 |
| **License** | AGPL-3.0 |
| **Language** | Python (80.7%), Shell, HTML, Less, TypeScript |
| **Commits** | 9,446+ |
| **First Release** | 2021 (fork of SearX) |

#### Architecture Overview
- **Core:** Python metasearch engine aggregating results from 200+ search services
- **Transport:** Flask-based web server with configurable engine backends
- **Privacy-first:** No tracking, no profiling, no cookies, no user data collection
- **Engine model:** Plugin architecture — each search engine is a Python module
- **Frontend:** Simple HTML/CSS/JS with Less theming, i18n support (Weblate)

#### Key Capabilities
- **Search Engines:** 200+ supported (Google, Bing, DuckDuckGo, Brave, Wikipedia, YouTube, GitHub, StackOverflow, etc.)
- **Categories:** General, Images, Videos, News, Maps, Music, Science, Files, Social Media, IT
- **Privacy:** No cookies, no tracking, POST searches, proxy support, Tor integration
- **Configuration:** Extensive settings for engines, categories, rate limiting, caching
- **API:** JSON API for programmatic access
- **Customization:** Themes, languages, search preferences
- **Autocomplete:** Search suggestions
- **Safe Search:** Configurable content filtering

#### API Design
- **Web UI:** Primary interface
- **JSON API:** `/search?format=json` for programmatic access
- **Plugin architecture:** Python engine modules
- **Admin:** Configuration via `settings.yml`, web interface

#### Resource Requirements
- **Python:** 3.8+
- **RAM:** ~256-512MB typical, scales with concurrent users
- **CPU:** Lightweight, single core sufficient
- **Docker:** Official image available, ~200MB

#### Self-hosting Complexity: **Easy-Medium**
- Docker: `docker run -p 8080:8080 searxng/searxng`
- Manual: Python venv + pip install
- Configuration via YAML files
- Extensive documentation

#### Community Health
- **Massive:** 31k stars, 170 issues, 51 PRs — very active
- **Mature:** 9.4k commits, well-established project
- **Community:** Matrix chat, Weblate translations, multiple public instances

#### Strengths
- Most comprehensive metasearch engine
- 200+ search engine integrations
- Battle-tested privacy-first design
- Easy Docker deployment
- JSON API for programmatic use
- Large community, excellent docs
- Currently used by this project

#### Weaknesses
- Metasearch only (no own index)
- Dependent on upstream search engines (rate limiting, blocking)
- No content extraction (returns links + snippets only)
- No JS rendering capability
- AGPL license (restrictive)

#### Best For
- Privacy-conscious search frontend
- Aggregating search results from multiple engines
- Self-hosted search portal for family/organization
- API-based search integration

---

### 7. Whoogle Search (benbusby/whoogle-search)

| Metric | Value |
|---|---|
| **Stars** | ~11,500 |
| **Forks** | ~1,000 |
| **License** | MIT |
| **Language** | Python |
| **Commits** | 919 |
| **First Release** | 2020 |
| **Status** | **FINAL RELEASE April 2026 — effectively dead** |

#### Architecture Overview
- **Core:** Google search proxy — scrapes Google results, strips ads/tracking/AMP/JS
- **Design:** Flask Python app with simple HTML frontend
- **Privacy:** Strips all Google tracking: ads, JS, cookies, AMP, UTM tags, referrer headers

#### Key Capabilities
- **Google Scraping:** Parses Google HTML results, removes all tracking
- **Privacy:** No ads, no JS, no cookies, no IP tracking, no AMP, no UTM tags
- **Proxy:** Tor, HTTP/SOCKS proxy support
- **Autocomplete:** Search suggestions
- **Bang Searches:** DDG-style `!<tag>` bangs with custom bang support
- **JSON API:** `format=json` or `Accept: application/json`
- **CSE Fallback:** Google Custom Search API (BYOK) as fallback
- **Site Alternatives:** Redirects to privacy-friendly frontends (Nitter, Invidious, etc.)

#### API Design
- **Web UI:** Primary interface
- **JSON API:** Accept header or `?format=json`
- **CLI:** `whoogle-search --port 5000`
- **Config:** Environment variables or web config UI

#### Resource Requirements
- **Python:** 3.x
- **RAM:** ~128-256MB
- **CPU:** Minimal
- **Docker:** `benbusby/whoogle-search:latest`, ~100MB

#### Self-hosting Complexity: **Easy**
- Docker: `docker run -p 5000:5000 benbusby/whoogle-search:latest`
- One-click deploys: Heroku, Render, Repl.it, Fly.io, Koyeb
- pipx: `pipx run whoogle-search`

#### Community Health
- **DEAD:** Final release April 2026
- **Reason:** Google aggressively blocked non-JS queries, all workarounds failed
- **11.5k stars** but effectively abandonware
- CSE BYOK mode still works but limited

#### ⚠️ DO NOT USE for new deployments. Consider SearXNG instead.

---

### 8. Stract (StractOrg/stract)

| Metric | Value |
|---|---|
| **Stars** | ~2,400 |
| **Forks** | ~62 |
| **License** | AGPL-3.0 |
| **Language** | Rust (94.7%), Svelte (2.9%), TypeScript (1.9%) |
| **Commits** | 1,309 |
| **Status** | **ARCHIVED April 2026 — read-only** |

#### Architecture Overview
- **Core:** Independent web search engine with its own crawler and index
- **Index:** Built on Tantivy (Rust-based inverted index, like Lucene but Rust-native)
- **Crawler:** Own web crawler (not dependent on third-party search APIs)
- **Frontend:** Svelte/TypeScript web UI
- **Funding:** NLnet/NGI0 Entrust (EU-funded), now discontinued

#### Key Capabilities
- **Own Index:** Crawls and indexes the web independently
- **Own Crawler:** Built-in web crawler for indexing
- **Keyword Search:** Respects search queries with advanced syntax (`site:`, `intitle:`)
- **Bang Searches:** DDG-style bang syntax
- **Optics:** Customizable search result weighting (blogs, indieweb, educational, etc.)
- **Trust Prioritization:** Link centrality-based ranking
- **Site Discovery:** Find sites similar to known ones
- **Tracker De-ranking:** De-rank websites with third-party trackers
- **Sidebars:** Wikipedia and StackOverflow sidebars

#### API Design
- **Web UI:** Primary interface
- **API:** REST API (docs at stract.com)
- **Self-hosted:** Docker Compose

#### Resource Requirements
- **Rust toolchain:** Required to build
- **RAM:** ~2-8GB for index (depends on index size)
- **CPU:** Multi-core recommended for crawling/indexing
- **Disk:** Large (web index)

#### Self-hosting Complexity: **Hard**
- Requires building from source (Rust + Node.js)
- Must crawl and build own index
- Index maintenance overhead
- Documentation for self-hosting is minimal

#### ⚠️ PROJECT ARCHIVED — no longer maintained. Historical interest only.

---

### 9. YaCy (yacy/yacy_search_server)

| Metric | Value |
|---|---|
| **Stars** | ~4,000 |
| **Forks** | ~479 |
| **License** | GPL-2.0+ |
| **Language** | Java (84.3%) |
| **Commits** | 14,851+ |
| **First Release** | 2003 |

#### Architecture Overview
- **Core:** Java-based distributed P2P search engine
- **Modes:** P2P (shared index), Portal (private index), Intranet (local network)
- **Index:** Built-in Solr/Lucene-like inverted index (custom Java implementation)
- **Crawler:** Built-in web crawler with scheduler for index freshness
- **Network:** P2P protocol for index exchange between peers
- **Frontend:** Servlet-based web UI (Jetty)

#### Key Capabilities
- **P2P Network:** Decentralized search network — all users contribute to and share the index
- **Own Crawler:** Full crawler with scheduling, depth control, robots.txt
- **Intranet Search:** Discover HTTP, FTP, SMB servers on local network
- **Privacy:** Opt-out from P2P, local-only search, no tracking
- **Administration:** Web-based admin interface at `:8090`
- **API:** HTTP/XML and HTTP/JSON APIs with documented endpoints
- **Multiple Formats:** HTML, JSON, XML, RSS, OPML output

#### API Design
- **Web UI:** Comprehensive admin + search interface
- **APIs:** HTTP/XML + HTTP/JSON on all pages (orange "API" icon)
- **Shell Scripts:** `/bin` subdirectory with example API clients
- **No modern SDK/REST:** APIs are page-based, not designed as modern REST

#### Resource Requirements
- **Java:** JDK 11+
- **RAM:** 1-4GB (Java heap, index)
- **CPU:** Multi-core recommended for indexing
- **Disk:** Large for search index

#### Self-hosting Complexity: **Medium**
- Docker: `docker run -d -p 8090:8090 -p 8443:8443 yacy/yacy_search_server:latest`
- Build from source: `git clone && ant clean all && ./startYACY.sh`
- P2P mode works immediately, Portal mode requires custom crawl config

#### Community Health
- **Alive:** Still active (204 open issues, 17 PRs)
- **Mature:** 14.8k commits, 23+ years of history
- **Slow:** Release cadence is slow, Java codebase shows age
- **Niche:** Small but dedicated community

#### Strengths
- Truly decentralized P2P architecture (unique)
- Own full-text search index and crawler
- 23+ years of development
- Intranet search capability
- Privacy-focused

#### Weaknesses
- Java (heavy, aging ecosystem)
- Slow performance compared to modern engines
- Search result quality lags behind metasearch engines
- Complex configuration
- Outdated web UI
- No LLM integration

#### Best For
- Decentralized search idealists
- Intranet/enterprise search
- Privacy-focused self-hosters
- Historical interest in P2P search

---

## Category C: Niche/New Frameworks (2024-2026)

### Notable Mentions

#### 10a. Reader LLM (Jina Reader)
Covered in Category A — the most innovative new framework in this space. Simple prefix-based API revolutionized how AI agents consume web content.

#### 10b. Firecrawl Agent Mode (2025)
Firecrawl's `/agent` endpoint represents the new paradigm: describe what you need, let the AI crawl and extract. This is the direction many tools are heading.

#### 10c. Crawl4AI Adaptive/Progressive Crawling (2025-2026)
v0.8.x introduced adaptive crawling (learns site patterns), prefetch mode (5-10x faster URL discovery), and crash recovery — innovative features not found in older frameworks.

#### 10d. Markdown-to-JSON Extraction Tools
- **markitdown** (Microsoft): Convert Office/PDF to markdown
- **docling** (IBM): Document understanding and conversion
- These complement crawling frameworks for downstream processing

#### 10e. Browser-Based Scraping Agents
- **Browserbase:** Headless browser platform (cloud, OSS components)
- **Playwright/Stealth:** De facto standards for JS rendering (used by Crawl4AI, Spider, Jina Reader)
- **nodriver:** Browser automation without WebDriver detection (used by this project)

#### What's Trending
1. **AI-First crawling:** LLM extraction, structured JSON output, agent-driven crawling
2. **Markdown as universal format:** Everything → markdown → LLM
3. **Anti-bot arms race:** Stealth modes, proxy escalation, CAPTCHA solving integrations
4. **MCP integration:** Direct connection to AI coding assistants
5. **Consolidation:** Firecrawl and Crawl4AI dominate; smaller tools struggle

---

## Comparative Matrix

| Feature | Crawl4AI | Spider | Firecrawl | Scrapy | Jina Reader | SearXNG | YaCy |
|---|---|---|---|---|---|---|---|
| **Search Capability** | ❌ | ❌ | ✅ Full | ❌ | ✅ Top-5 | ✅ Meta (200+) | ✅ Own Index |
| **Crawl Capability** | ✅ Deep (BFS/DFS/BestFirst) | ✅ Deep (depth/limit) | ✅ Full (map+crawl+batch) | ✅ Full (spiders+rules) | ❌ Single URL | ❌ | ✅ Own Crawler |
| **Single-page Fetch** | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ (snippets only) | ✅ |
| **JS Rendering** | ✅ Playwright (all engines) | ✅ Chrome (optional) | ✅ Headless Browser | ✅ Via plugin (Playwright) | ✅ Puppeteer + curl | ❌ | ❌ |
| **Anti-bot/Stealth** | ✅ 3-tier + proxy escalation | ✅ Stealth mode + proxy | ✅ Cloud tier (OSS limited) | ⚠️ Via middleware | ✅ curl-impersonate | ❌ | ❌ |
| **LLM Extraction** | ✅ Full (any provider) | ⚠️ Basic | ✅ Agent + structured | ❌ Custom code | ❌ | ❌ | ❌ |
| **Screenshots** | ✅ | ✅ | ✅ | ⚠️ Via plugin | ✅ Via headers | ❌ | ❌ |
| **Rate Limiting/Politeness** | ✅ Built-in | ✅ Built-in | ✅ Built-in | ✅ AutoThrottle | ⚠️ Configurable | ✅ Per-engine | ✅ Built-in |
| **License** | Apache 2.0 | MIT | AGPL-3.0 | BSD-3-Clause | Apache 2.0 | AGPL-3.0 | GPL-2.0+ |
| **Language** | Python | Rust | TypeScript+Python+Rust | Python | TypeScript | Python | Java |
| **Resource Footprint** | Medium (browser) | Low-Medium | High (multi-service) | Medium | Medium | Low | High (Java) |
| **Maturity/Stability** | Medium (2023) | Medium | High (2024, well-funded) | Very High (2008) | Medium (2024) | High (2021) | Very High (2003) |
| **Setup Complexity** | Easy-Medium | Easy-Medium | Medium-Hard | Medium | Easy | Easy-Medium | Medium |
| **API/SDK** | Python + REST + CLI | Rust+Python+Node+CLI | REST + 6 SDKs | Python framework only | URL prefix + headers | Web + JSON | Web + XML/JSON |
| **Docker** | ✅ Official | ✅ Community | ✅ Docker Compose | ❌ (DIY) | ✅ Official | ✅ Official | ✅ Official |
| **Monitoring** | ✅ Dashboard | ⚠️ Cloud only | ✅ Cloud tier | ❌ (DIY + Scrapyd) | ❌ | ❌ | ✅ Web Admin |
| **MCP Integration** | ✅ | ✅ (spider_mcp) | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Active Development** | ✅ Very Active | ✅ Active | ✅ Very Active | ✅ Active | ✅ Active | ✅ Very Active | ⚠️ Slow |
| **OSS-Friendly** | ✅ Full-featured OSS | ✅ OSS = Cloud-lite | ⚠️ OSS intentionally limited | ✅ Full OSS | ✅ OSS (storage stripped) | ✅ Full OSS | ✅ Full OSS |

### License Compatibility Quick Reference

| License | Commercial Use | Modification | Distribution | Patent Grant |
|---|---|---|---|---|
| Apache 2.0 (Crawl4AI, Jina Reader) | ✅ | ✅ | ✅ | ✅ |
| MIT (Spider, Whoogle) | ✅ | ✅ | ✅ | ❌ (no explicit) |
| BSD-3-Clause (Scrapy) | ✅ | ✅ | ✅ | ❌ (no explicit) |
| AGPL-3.0 (Firecrawl, SearXNG, Stract) | ⚠️ Must open source | ✅ | ✅ (under AGPL) | ✅ |
| GPL-2.0+ (YaCy) | ⚠️ Must open source | ✅ | ✅ (under GPL) | ❌ |

---

## Decision Framework

### If you need... → Use

| Need | Primary Recommendation | Alternative |
|---|---|---|
| **LLM-ready markdown from URLs** | Crawl4AI | Jina Reader |
| **Structured data extraction (AI)** | Crawl4AI (LLMExtractionStrategy) | Firecrawl (Agent mode) |
| **Fastest crawling (Rust project)** | Spider | — |
| **Full web data platform (search+scrape+crawl)** | Firecrawl | SearXNG + Crawl4AI |
| **Maximum customization/extensibility** | Scrapy | Crawl4AI (hooks) |
| **Easiest Docker deployment** | Jina Reader | Crawl4AI |
| **Privacy search frontend** | SearXNG | — (Whoogle is dead) |
| **Own search index** | YaCy (P2P) | — (Stract archived) |
| **MCP/AI agent integration** | Crawl4AI or Firecrawl | Spider |
| **Rust-native integration** | Spider | — |
| **Low-resource environments** | Jina Reader | Spider |
| **Most permissive license** | Scrapy (BSD) | Spider (MIT) |
| **Production at scale** | Scrapy (battle-tested) | Firecrawl (cloud) |
| **All-in-one for this project** | Crawl4AI (crawl+fetch+LLM) + SearXNG (search) | Firecrawl (if AGPL acceptable) |

---

## Recommendations for Kindly Web Search MCP

Given this project already uses SearXNG for search, the natural complementary tools are:

1. **Crawl4AI** — Best fit for content extraction with LLM-ready markdown, JS rendering, anti-bot. Apache 2.0 compatible. Python-native matches the project stack.
2. **Jina Reader** — Simpler alternative for single-URL fetch. Less capable for crawling but trivial to deploy. Could serve as a lighter content extraction backend.
3. **Spider** — If performance becomes a bottleneck, the Rust-native crawler could accelerate high-volume crawling.

**Not recommended:**
- Firecrawl: AGPL license conflict, heavy deployment, OSS is intentionally limited
- Scrapy: Overkill for this MCP server's use case (single-URL fetch, not large-scale crawling)
- YaCy/Stract: Too heavy, Java/Rust build complexity, search quality inferior to SearXNG

---

## Sources

- GitHub repositories (direct README analysis): crawl4ai, spider-rs, firecrawl, scrapy, jina-ai/reader, searxng, benbusby/whoogle-search, StractOrg/stract, yacy_search_server
- Official documentation pages: docs.crawl4ai.com, docs.firecrawl.dev, docs.searxng.org, spider.cloud, jina.ai/reader, scrapy.org, yacy.net
- Repository metadata: stars, forks, license, commits, contributors as of June 2026
