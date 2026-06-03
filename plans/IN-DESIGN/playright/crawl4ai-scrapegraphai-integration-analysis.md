# Crawl4AI & ScrapeGraphAI Integration Analysis

## Dependency Check: Do they use Playwright?

**Both do, confirmed from pyproject.toml:**

| Library | Playwright deps |
|---------|----------------|
| **Crawl4AI** | `playwright>=1.49.0`, `patchright>=1.49.0`, `playwright-stealth>=2.0.0` |
| **ScrapeGraphAI** | `playwright>=1.57.0`, `undetected-playwright>=0.3.0` |

Playwright is the core browser engine in both. This means:
- A migration to Playwright (replacing nodriver) is **already required** to add either library
- Both can share one Playwright installation — no redundant browser downloads
- `playwright install chromium` handles the binary — no more custom browser discovery/Path hacks

---

## What each replaces

### Crawl4AI replaces 4 files (~2,800 lines)

| Current File | Lines | Replaced By |
|---|---|---|
| `scrape/nodriver_worker.py` | 1,279 | `AsyncWebCrawler` (in-process Playwright, no subprocess IPC) |
| `scrape/universal_html.py` | 1,033 | Built-in JS render + HTML→Markdown pipeline |
| `scrape/chromium_pool.py` | 379 | Built-in browser instance pool (acquire/release) |
| `scrape/extract.py` | 150 | 10 extraction strategies: LLM, Cosine, BM25, CSS, Regex, etc. |

**Net: 4 files deleted, ~2,800 lines removed.** Browser/scraping management becomes a pip dependency.

**New capabilities from Crawl4AI:**
- Site-wide recursive crawl (BFS/DFS with depth/comma control)
- Structured JSON extraction via LLM (schema-validated, prompt-defined)
- Screenshot & PDF generation
- Network interception (block ads, trackers, images)
- JavaScript actions (click, scroll, type, hover)
- CSS selector & regex extraction strategies
- Hook system for custom JS injection at lifecycle events
- Built-in rate limiting, caching, and content filtering
- Anti-bot evasion (stealth, user-agent rotation, proxy support)

### ScrapeGraphAI replaces extract.py (~150 lines)

- Does **not** replace the browser layer — delegates to Playwright
- Replaces `extract.py` with **prompt-based extraction graphs**
- Define extraction intent in natural language, LLM infers DOM selectors
- Works on heterogeneous sites where CSS selectors change frequently

**Graph types:**
- `SmartScraperGraph` — single-page extraction from prompt
- `SearchGraph` — search → scrape multiple results
- `ScriptCreatorGraph` — generates reusable extraction scripts

**Tradeoff:** Slower per-request (LLM call to infer selectors each time), but DOM-resilient. Best for complex or unstable page structures, not routine extraction.

---

## Combined picture

```
┌─────────────────────────────────────────────────────────────┐
│                   Current Architecture                       │
│                                                              │
│  fetch_pipeline.py → Stage 8 → universal_html.py            │
│                                       ├─ nodriver_worker.py  │
│                                       ├─ chromium_pool.py   │
│                                       └─ extract.py         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                   With Crawl4AI + optional SGA               │
│                                                              │
│  fetch_pipeline.py → Stage 8 → AsyncWebCrawler (crawl4ai)   │
│                                       │                     │
│                                       ├─ Playwright (shared) │
│                                       │                     │
│  NEW: crawl tool → Crawl4AI (BFS/DFS recursive crawl)       │
│  NEW: extract tool → Crawl4AI LLMStrategy or SGA            │
└─────────────────────────────────────────────────────────────┘
```

- Crawl4AI is the **primary replacement** — browser engine + extraction + crawling in one dependency
- ScrapeGraphAI is **supplemental** — adds prompt-based extraction graphs for hard-to-scrape pages
- Both share Playwright — no extra browser binary overhead
- trafilatura + BS4 fallback in `extract.py` can remain as a no-browser extraction path (Stage 6 in pipeline)

---

## Dependency footprint comparison

| Current | Lines | Adding Crawl4AI | Lines |
|---------|-------|-----------------|-------|
| nodriver | 0 (external) | playwright | 0 (external) |
| nodriver_worker.py | 1,279 | — | 0 |
| universal_html.py | 1,033 | — | 0 |
| chromium_pool.py | 379 | — | 0 |
| extract.py | 150 | crawl4ai strategies | ~50 (wrapper) |
| **Total** | **~2,841** | | **~50** |

**~98% code reduction** in the browser layer.
