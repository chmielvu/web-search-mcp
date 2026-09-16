# crawlctl — `crawl4ai-rag-mcp`

**FastMCP server that exposes a self-hosted Crawl4AI instance to AI agents as a
scrape → clean → RAG-ready-Markdown pipeline.**

```
   AI agent (Claude / Cursor / any MCP client)
        │  MCP (stdio or streamable HTTP)
        ▼
   crawl4ai-rag-mcp  ── 5 tools: capabilities · scrape · crawl_site · clean_text · corpus_status
        │  REST (auto-detected server version, bearer-optional)
        ▼
   Self-hosted Crawl4AI (unclecode/crawl4ai, port 11235)
        │
        ▼
   <corpus>/pages/<host>/<slug>.md   ← YAML front matter + clean body
   <corpus>/quarantine/…             ← low-score pages (same format)
   <corpus>/manifest.json            ← incremental re-scrape support
   <corpus>/state/boilerplate.json   ← per-site template DB (warms across runs)
```

Chunking and indexing are intentionally **out of scope** — every emitted file
carries `title, source_url, content_type, score, content_hash, outline, links`
front matter so any downstream chunker/indexer ingests it without re-parsing.

## Why not just use Crawl4AI's fit_markdown?

`fit_markdown` works at the DOM level at fetch time and is good but leaky —
leakage is content-type dependent (docs sidebars, wiki citation markers, blog
CTAs, news carousels all survive to different degrees). crawlctl adds defense
in depth: DOM filtering (server, version-gated config) → extraction
arbitration (Crawl4AI vs trafilatura on `cleaned_html`) → tiered markdown
cleaning → type-calibrated quality scoring. The full failure-mode catalog and
the research behind every countermeasure is in [RESEARCH.md](RESEARCH.md).

## Install

```bash
pip install -e .                 # core (fastmcp, httpx, markdown-it-py, pyyaml, ftfy)
pip install -e ".[chef]"         # + Chonkie MarkdownChef structure backend (optional)
pip install -e ".[trafilatura]"  # + extraction arbitration candidate (recommended)
pip install -e ".[tiktoken]"     # + exact token counts (else chars/3.8 estimate)
pip install -e ".[polish]"       # + optional mdformat final pass (off by default)
pip install -e ".[dev]"          # pytest suite
```

Python ≥ 3.10.

## Configuration (env)

| Variable | Default | Meaning |
|---|---|---|
| `CRAWL4AI_BASE_URL` | `http://localhost:11235` | Self-hosted Crawl4AI server |
| `CRAWL4AI_API_TOKEN` | – | Bearer token; header sent **only when set** |
| `CRAWL4AI_TIMEOUT` | `180` | Per-request timeout (s) |
| `CRAWLCTL_PUBLISH_BAND` | `65` | Publish threshold (0–100) |
| `CRAWLCTL_REVIEW_BAND` | `40` | Review threshold |
| `CRAWLCTL_DISABLE_CHONKIE` | `0` | Force native cleaner backend |
| `CRAWLCTL_DISABLE_FTFY` | `0` | Disable ftfy unicode pass |
| `CRAWLCTL_MDFORMAT` | `0` | Enable optional mdformat polish pass |

## Running

```bash
crawl4ai-rag-mcp                 # stdio (Claude Desktop / Claude Code / Cursor)
crawl4ai-rag-mcp --http --port 8051   # streamable HTTP at /mcp + GET /health
```

Claude Desktop / Claude Code registration:

```json
{
  "mcpServers": {
    "crawl4ai-rag": {
      "command": "crawl4ai-rag-mcp",
      "env": {
        "CRAWL4AI_BASE_URL": "http://your-server:11235",
        "CRAWL4AI_API_TOKEN": "<optional>"
      }
    }
  }
}
```

## The 5 tools (agent-first surface)

| Tool | Purpose | Key params |
|---|---|---|
| `capabilities` | Discover the server (version, live endpoints, capability flags, cleaner backends) + tool guide. Call first. | – |
| `scrape` | 1..100 URLs → cleaned, scored markdown inline; optional full files. `mode`: `auto`/`browser` (tuned server-side extraction) · `fast` (cheap `/md`) · `bm25` (query-filtered). | `urls, mode, query, cleaner, link_mode, image_policy, citation_mode, content_type, save_dir, max_chars, options, fit` |
| `crawl_site` | Sitemap / llms.txt / URL / URL-list file → corpus directory. Incremental via manifest; `two_pass` warms the boilerplate DB (recommended first run); `deep` = client-side BFS (uniform across server versions, incl. 0.9.x where REST deep-crawl is forbidden). | `source, out_dir, deep, max_depth, max_pages, cleaner, extractor, keep_raw, two_pass, force, url_include/exclude, …` |
| `clean_text` | Clean + score markdown you already have (no scraping). Returns cleaned text, edit report, 0–100 score. | `markdown, source_url, cleaner, link_mode, image_policy, citation_mode, content_type, save_path` |
| `corpus_status` | Manifest summary: per-status counts + lowest-score sample for review. | `corpus_dir` |

Design contract: few orthogonal tools, rich parameters with RAG-tuned
defaults, honest MCP annotations, structured JSON outputs, actionable errors
(auth hints, 0.9.x config-gate explanations, batch limits), progress
notifications on corpus builds.

## Quality scoring

Composite 0–100, type-calibrated (docs/wiki/blog/news/generic weights), with
FineWeb/Gopher/C4 page-level heuristics (terminal-punctuation ratio, short-line
ratio, duplicate-line fraction, symbol ratio, stopword ratio, word floors).
Bands: **publish ≥ 65 · review 40–65 · quarantine < 40** (hard word floor
applies). Every file's front matter carries the value, band and human-readable
notes — auditable end to end.

## Cleanliness guarantees (per document)

single H1 · ATX headings, no level gaps · absolute links, tracking params
stripped, `javascript:` removed · TOC anchor links flattened · navigation
runs and breadcrumbs dropped · wiki citations/chrome stripped · docs copy
buttons removed · HTML tables → pipe tables · code fences balanced and never
touched · mojibake/NBSP/zero-width/ligatures normalized (ftfy) · HTML debris
removed · per-site repeated boilerplate dropped (two-pass) · FineWeb
dup-line collapse · blank-run normalization.

## Development

```bash
pip install -e ".[dev,trafilatura,chef]"
pytest                # 99 tests: cleaner goldens, scorer calibration,
                      # client auto-detect (mocked server), pipeline E2E, MCP wiring
```

Project layout:

```
crawlctl/
├── client.py      Crawl4AI REST client: auto-detect recipe, bearer-optional auth,
│                  retries, 5-key markdown handling, version capability flags
├── knobs.py       Version-gated config builders (0.6→0.9 envelopes), RAG presets,
│                  URL scoping, slugify, sitemap/llms.txt discovery
├── siteprof.py    Content-type detection (docs/wiki/blog/news/generic) + junk patterns
├── cleaner.py     Tiered cleaner: T0 unicode/HTML → T1 AST line surgery →
│                  T2 statistical block cleaning → T3 polish
├── scorer.py      Type-calibrated 0–100 score, FineWeb signals, candidate arbitration
├── chef.py        Chonkie MarkdownChef structure-oracle backend + fallbacks
├── extract.py     trafilatura arbitration candidate from cleaned_html
├── storage.py     Front-matter writer (atomic), BoilerplateDB, Manifest, report
├── pipeline.py    discover → acquire → extract(arbitrate) → clean → score → write
├── service.py     Framework-free tool logic (unit-testable)
└── mcp_server.py  FastMCP wiring: 5 tools, annotations, lifespan, stdio/HTTP
```

## Notes & honest limitations

* **Chonkie MarkdownChef is a parser, not a cleaner** (verified against
  chonkie 1.7.0 — no `cook/clean` methods exist in any release). The `chef`
  backend uses it for structure-aware table protection + stats; it never
  replaces the native cleaner. See RESEARCH.md §2.1.
* **0.9.x REST gate**: `js_code`, sessions, proxies, server-side deep-crawl
  strategies are forbidden over REST; crawlctl strips them with a warning and
  implements deep crawling client-side.
* `/md` (`mode="fast"`) uses server-default filters — it is the cheap path,
  not the tunable one; use `mode="browser"` for the tuned extraction config
  and trafilatura arbitration.
* LLM-as-judge quality scoring is deliberately excluded (FineWeb evidence:
  distill offline instead); the score's `notes[]` expose the features you
  would train that classifier on.
