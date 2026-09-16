# crawlctl Research: Crawl4AI server API, markdown post-processing stack, SOTA pipeline practices

> Research basis for crawlctl 0.3.0 — compiled 2026-09 from primary sources
> (Crawl4AI server source at tags 0.6.3/0.7.5/0.8.0/main@0.9.3, PyPI sdists,
> official benchmarks, HuggingFace FineWeb publications, gofastmcp.com docs).
> Every design decision in the code links back to a section here.

---

## 1. Self-hosted Crawl4AI server: endpoints & knobs

### 1.1 Correction to common assumptions

The self-hosted API is **not** the library API. Verified against
`deploy/docker/server.py` (Docker image `unclecode/crawl4ai`, default port
**11235**, healthcheck `GET /health`):

* PyPI latest is **0.9.3** (0.7.4–0.7.8 Aug 2025 → 0.8.0 Jan 2026 → 0.9.0 Jun 2026).
* **`/token.json`, `/config.json`, `/llmtxt` do not exist.** The prototype
  referenced all three. Real endpoints: `POST /token` (JWT mint),
  `POST /config/dump` (config validation probe), `GET /schema` (capability
  introspection). llms.txt generation is an SDK/CLI application, not a
  server endpoint.

### 1.2 Endpoint map

| Endpoint | Method | Request → Response | Notes |
|---|---|---|---|
| `/health` | GET | – → `{status, timestamp, version}` | Only reliably public path on all versions; version anchor for auto-detection |
| `/metrics` | GET | – → Prometheus text | auth-gated on 0.9.x |
| `/token` | POST | `{email, api_token?}` → `{access_token, token_type}` | 0.9.x requires matching static token |
| `/config/dump` | POST | `{type:"CrawlerRunConfig"\|"BrowserConfig", params{...}}` → normalized dump | Pre-flight config validation; 400 detail names rejected fields |
| `/schema` | GET | – → `{browser: dump, crawler: dump}` | Unauthenticated on open servers (≤0.8.9 default posture); 401 on 0.9.x |
| `/crawl` | POST | `{urls[1..100], browser_config?, crawler_config?}` → `{success, results[], server_processing_time_s, ...}` | `stream:true` in config → NDJSON |
| `/crawl/stream` | POST | same → NDJSON CrawlResult lines + final `{status:"completed"}` | |
| `/md` | POST | `{url, f:"raw"\|"fit"\|"bm25"\|"llm", q?, c?}` → `{url, filter, query, cache, markdown, success}` | Cheap path; `fit` uses server-default PruningContentFilter, `bm25` requires `q`; **not client-tunable** |
| `/html` | POST | `{url}` → `{html, url, success}` | Schema-preprocessed HTML |
| `/screenshot`, `/pdf`, `/execute_js` | POST | … | Out of scope (core is markdown); `/execute_js` 403 by default since 0.8.7 |
| `/llm/{url}`, `/ask` | GET | … | LLM-backed; out of scope |
| `/artifacts/{id}` | GET | binary | 0.9.0+ artifact store (replaces `output_path`) |
| `/mcp/sse\|ws\|schema` | – | – | Crawl4AI ships its own MCP server since 0.6.0 |
| `/openapi.json`, `/docs` | GET | – | **401-gated on 0.9.x — never rely on it for discovery** |

### 1.3 Config envelope

* Top-level scalars pass through flat (`word_count_threshold`, `page_timeout`, …).
* Nested strategies/objects use the `{"type": "Name", "params": {...}}` envelope
  (`PruningContentFilter`, `BM25ContentFilter`, `DefaultMarkdownGenerator`).
* Enums wrap as `{"type": "CacheMode", "params": "bypass"}`.

### 1.4 CrawlerRunConfig knobs that matter for RAG markdown scraping

| Knob | Recommended | Why |
|---|---|---|
| `word_count_threshold` | 10–20 | Default (~200) drops short docs headings/steps |
| `excluded_selector` | nav/footer/aside/.toc/.breadcrumb/.cookie-banner/… | Server-side chrome removal before markdown conversion |
| `markdown_generator` | `DefaultMarkdownGenerator(content_filter=PruningContentFilter(threshold_type:"dynamic"), options{body_width:0})` | `body_width:0` = no hard wrapping (hard-wrapped text breaks heading-aware chunkers); dynamic threshold adapts to page density |
| `PruningContentFilter` | `threshold=0.45–0.48, threshold_type="dynamic"` | DOM-level boilerplate pruning (Kohlschütter text/link-density lineage) |
| `BM25ContentFilter` | `user_query, bm25_threshold=1.0, use_stemming=True` | News/query-scoped extraction |
| `cache_mode` | `bypass` | Fresh fetches for corpus refreshes |
| `check_robots_txt` | `true` | Compliance |
| `page_timeout` | 30000 (≤60000 on 0.9.x) | 0.9.x clamps server-side |
| `target_elements` (≥0.8) | `["main", "article"]` | Scoped extraction when schema shows support |
| `css_selector` / `excluded_selector` | as needed | DOM scoping |

### 1.5 The 0.9.x "untrusted" gate (critical)

0.9.x rejects with HTTP 400: `js_code`, `session_id`, `deep_crawl_strategy`,
`proxy_config`, `magic`, `base_url`, `simulate_user`, `cookies`, `headers`,
all `LLM*`/`Proxy*`/`DeepCrawl*` strategy types. Unknown fields are silently
dropped; `page_timeout` clamps to ≤60000 ms; viewport to ≤4000. **Deep
crawling over REST is impossible on 0.9.x** → crawlctl implements deep
crawling as client-side BFS over `result.links.internal` uniformly on all
versions (also removes per-version code paths).

### 1.6 Result payload

`result.markdown` is always the 5-key dict
`{raw_markdown, markdown_with_citations, references_markdown, fit_markdown, fit_html}`
(`fit_markdown` populated only when a filter is configured). Other fields:
`success, url, html, cleaned_html, metadata, links{internal,external},
error_message, session_id, status_code, redirected_url, tables (0.6.1+),
cache_status`. `cleaned_html` is what crawlctl feeds trafilatura for the
arbitration candidate.

### 1.7 Version-detection recipe (implemented in `client.detect()`)

1. `GET /health` (no auth) → version string.
2. `GET /schema` unauthenticated → 200: open server (≤0.8.9 posture);
   401: 0.9.x posture → retry with Bearer if token configured.
3. Capability flags derived from the parsed version
   (`untrusted_gate ≥0.9`, `generator_style_filter ≥0.7`,
   `target_elements ≥0.8`, …); `POST /config/dump` available as an optional
   pre-flight probe (its 400 detail names exactly which power fields the
   server would reject).
4. Never touch `/openapi.json` for discovery (401-gated on 0.9.x).

Auth model: bearer-optional. `Authorization: Bearer <token>` attached only
when `CRAWL4AI_API_TOKEN` is set; health checks never send it (keeps posture
detection clean). On 0.9.x a token-less server binds loopback and refuses
non-loopback startup.

---

## 2. Markdown post-processing libraries: reliability comparison

### 2.1 Chonkie MarkdownChef — verified: it is a parser, not a cleaner

The prototype assumed a cleaning API (`cook/clean/sanitize/transform` method
probing). **Verified against chonkie 1.7.0 source and PyPI sdist diffing
(1.2.1 → 1.7.0): no such methods exist in any release.**

* Introduced v1.3.1 (2025-09-27). Import: `from chonkie import MarkdownChef`.
* API: `MarkdownChef(tokenizer="character").parse(text) -> MarkdownDocument`
  with `.tables` / `.code` / `.images` / `.chunks` carrying char-index spans
  (`start_index`, `end_index`) over the preserved input text.
* It is a regex-based segmenter feeding chonkie's chunkers. It performs no
  normalization, no link-debris removal, no cleaning.
* Known weaknesses (validated in our tests): 4+ backtick/tilde fences yield
  `None` spans; escaped pipes can confuse table regexes.

**crawlctl integration (`chef.py`)**: MarkdownChef is used as a *structure
oracle* — table spans become hash-protected regions the native cleaner must
not junk-drop, and structure stats (n_tables/n_code/n_images) enrich the
result and front matter. Full native cleaning always runs. Backends:
`native` (default), `markdownchef` (structure assist), `auto` (markdownchef
when chonkie importable). The adapter validates every span (non-None,
in-range, content-preserving) and falls back gracefully on mismatch.

### 2.2 Library comparison

| Library | Role | Reliability | Maintenance 2025–26 | Verdict |
|---|---|---|---|---|
| **markdown-it-py 4.2.0** | CommonMark AST/token stream | **A** | Active; Google Assured OSS | Primary tool for AST-guided line surgery; `Token.map` is line-based; no serializer needed for our no-reflow design |
| **mdformat 1.0.0 (+mdformat-gfm)** | Idempotent md→md formatter | **A** | Active (executablebooks) | Only validated md→md serializer; byte-changing but rendered-content-preserving (`validate=True`); optional polish pass in crawlctl (off by default, `CRAWLCTL_MDFORMAT=1`) |
| **trafilatura 2.2.0** | HTML main-content extraction → markdown | **A** | Active; Apache-2.0 | Benchmark leader (official 2026-08 eval: F1 0.924 standard / 0.925 favor_precision vs justext 0.862, readability-lxml 0.826, resiliparse 0.811, html2text 0.663). Used for extraction arbitration on `cleaned_html` |
| **ftfy 6.3.1** | Mojibake / unicode fixing | **A** | Stable | First-pass unicode normalization. ⚠ must call with `unescape_html=False` or it decodes entities inside markdown code spans |
| markdownify 1.2.3 | HTML→MD rescue converter | B+ | Active | Good lean fallback; produces pipe tables; not needed while trafilatura covers HTML |
| html2text 2025.4.15 | HTML→MD | C | Sporadic | **Avoid** — no GFM tables; extraction F1 worse than raw HTML |
| readability-lxml 0.9 | DOM main-content | B | Active | Only as last-resort arbitration fallback |
| jusText 3.0.2 | Paragraph classifier | B | Active | Tuned defaults beat generic; short-block bias hurts lists/captions; ideas absorbed (thresholds), not the lib |
| resiliparse 1.0.9 | Ultra-fast HTML→text | B precision | Active | DCLM-scale tool; overkill here |
| unstructured / docling | Heavy multi-format parsers | B+ | Active | Dependency weight (torch-family) unjustified for an MCP server |
| chonkie 1.7.0 | Chunkers + chef segmenters | B | Active | Parser only (see 2.1) |

### 2.3 Recommended stack (implemented)

1. **AST surgery**: markdown-it-py (token-mapped, line-preserving edits).
2. **Unicode/mojibake**: ftfy (`unescape_html=False`) + curated mojibake map fallback.
3. **HTML→MD rescue / arbitration**: trafilatura on `cleaned_html` (extraction-level, not cleaning-level).
4. **Syntax normalization**: native deterministic passes; optional mdformat polish.

Rationale: the primary extraction is Crawl4AI's DOM-level `fit_markdown`, so
post-processing is markdown-native ~always; HTML tools enter only as a second
opinion. Defense in depth = DOM filtering (server) + AST surgery + statistical
block cleaning + scoring (client).

### 2.4 Failure modes the native cleaner defends against

1. Full re-serialization destroys tables/code → line-count-preserving T1 surgery, deletions confined to T2 line ranges.
2. Regex segmenters misparse 4+ backtick fences / escaped pipes → fence mask from markdown-it, chef spans validated.
3. Reference-style link definitions and link-target text invisible to naive regex → label-aware full-link regex (`[label](target)`) everywhere; link targets excluded from breadcrumb matching.
4. ftfy decoding entities inside code spans → `unescape_html=False`, own code-span-protected entity decode.
5. Arbitration losing type calibration → `compare_candidates` carries each candidate's detected content type.

---

## 3. Scrape → clean → index pipeline practices (2025/2026 SOTA)

### 3.1 Page-level heuristics that survived empirical scrutiny

FineWeb (arXiv 2406.17557) ablated 50+ candidate stats down to ~16
metric-threshold pairs with real page-level signal. The ones crawlctl
implements (page level, not corpus level):

| Heuristic | Source | Threshold | Where in crawlctl |
|---|---|---|---|
| Word count floor | Gopher/FineWeb | hard reject < 40 words after cleaning | scorer hard floor |
| Terminal-punctuation line ratio | FineWeb (best single cheap signal) | flag < 0.12 | scorer `prose` |
| Short-line ratio (<30 chars) | FineWeb | flag ≥ 0.67 (docs exempt) | scorer `prose` ×0.6 |
| Duplicate-line char fraction | Gopher 0.20 / FineWeb 0.1 | flag ≥ 0.10 | scorer `hygiene` + notes |
| Symbol-to-word ratio | Gopher ≤ 0.1 | soft flag | scorer `hygiene` |
| Newline:word ratio | FineWeb > 0.3 | soft flag | scorer `hygiene` |
| Stopword ratio + sentence sanity | classic readability | keep | scorer `prose` |
| Avg word length 3–10 | Gopher | keep | scorer `prose` |
| JS/curly-bracket debris | C4 curly-bracket rule | block-level | cleaner T2 pass B |
| Duplicate-line runs | FineWeb char-dup | collapse runs > 3 | cleaner T2 pass G |
| Per-site template blocks | Kohlschütter 2010 / CommonCrawl | ≥50% of ≥3 pages | BoilerplateDB (two-pass) |
| MinHash dedup | FineWeb | per-crawl, not global | out of scope (content-hash in manifest covers exact dedup) |

FineWeb's meta-finding on LLM-judges: annotate offline → distill to a small
classifier → threshold (FineWeb-Edu F1 82%, GneissWeb 85.8%). Never per-page
LLM judging in an ingestion path. crawlctl's heuristic composite is the right
cost point for an MCP tool; the score's `notes[]` give a future distilled
classifier its features.

### 3.2 Boilerplate removal ranking (verified benchmarks)

1. trafilatura 2.2.0 — F1 0.924 (official 990-doc bench, 2026-08); favor_precision P .925.
2. jusText tuned — F1 0.862 (defaults: MAX_LINK_DENSITY 0.2, LENGTH_LOW 70, LENGTH_HIGH 200).
3. readability-lxml — 0.826; resiliparse — 0.811 (recall-heavy).
4. Per-site repeated-block DBs: no published benchmark — production
   enhancement tier. crawlctl's BoilerplateDB implements Kohlschütter-style
   repetition with a per-page idempotency fix (the prototype double-counted
   blocks across two-pass runs) and a ≥50%-of-pages requirement.

### 3.3 RAG-ready markdown conventions (Firecrawl / Jina / Crawl4AI / indexers)

* Firecrawl v2 defaults `onlyMainContent: true`; unwrapped body text; pipe tables.
* Jina Reader: `Title:`/`URL Source:`/`Markdown Content:` headers; alt-text-only images; opt-in front matter.
* Crawl4AI emits **no** front matter → crawlctl's rich front matter (title,
  source_url, content_type, score, content_hash, outline, links, cleaning
  edits) is the differentiator; content hashes mirror Firecrawl's
  change-tracking need.
* Measurably helpful hygiene for header-aware splitters
  (`MarkdownHeaderTextSplitter` / `MarkdownNodeParser`): single H1, normalized
  heading hierarchy (ATX, no gaps), pipe tables, no bare link dumps, no hard
  wrapping, image policy applied, dedup by content hash.

### 3.4 Scoring bands

No published RAG band standard exists. Research recommendation: Reject < 40 ·
Review 40–65 · Publish ≥ 65, heuristics for the bottom half, LLM-judge only
for the borderline band if at all. crawlctl defaults: publish ≥ 65, review ≥
40 (`CRAWLCTL_PUBLISH_BAND` / `CRAWLCTL_REVIEW_BAND` env-tunable), calibrated
on the test fixtures: real docs/wiki/blog/news pages score 69.9–74.1
(publish); a pure navigation page scores 35.0 (reject via hard floor).

### 3.5 FastMCP server design (verified against fastmcp 4.0.3)

* **Package**: PyPI `fastmcp` (standalone; jlowin/fastmcp → PrefectHQ/fastmcp;
  gofastmcp.com). 4.0.3 current; 2.x→3.x→4.x lineage. The official `mcp` SDK
  v1 bundled FastMCP 1.0 at `mcp.server.fastmcp` — SDK v2 removed that path.
  crawlctl imports `from fastmcp import FastMCP` with an SDK-v1 fallback shim.
* **Transports**: `mcp.run(transport="stdio" | "http" | "sse" |
  "streamable-http")`; `"http"` is the modern streamable-HTTP endpoint at
  `/mcp`; `@mcp.custom_route("/health")` for HTTP deploys.
* **Annotations**: `@mcp.tool(annotations=ToolAnnotations(readOnlyHint=…,
  destructiveHint=…, idempotentHint=…, openWorldHint=…))` — self-reported UX
  hints per the 2025-06-18 spec, not security guarantees.
* **Tool design principles applied** (Anthropic + AWS guidance + fastmcp docs):
  1. Few tools, rich params (context budget: every tool definition eats agent context; ~≤8 params/tool guideline, power tools excepted with strong defaults).
  2. Progressive disclosure via params (`max_chars`, `save_dir`, `options`) instead of extra tools.
  3. Outcome-oriented names + descriptions that state purpose, constraints and failure modes (descriptions are the model's retrieval key).
  4. Honest annotations (`openWorldHint=True` for web-touching tools).
  5. Structured returns: dict returns become `structuredContent`; string JSON bodies kept for wide client compatibility.
  6. `ToolError` with actionable messages (auth hints, config-gate explanations, batch limits); unexpected exceptions wrapped with a check-server-logs pointer.
  7. Progress reporting (`ctx.report_progress(done, total, message)`) for corpus builds; lifespan holds the shared client + auto-detected server profile.

crawlctl surface = **5 tools**: `capabilities`, `scrape` (fast/browser/bm25
modes fold three prototype tools into one), `crawl_site`, `clean_text`
(folds clean+score), `corpus_status`. Screenshot/PDF/JS/llmtxt were dropped
per scope decision (scrape→clean→markdown only); they remain reachable via
raw HTTP if needed.

---

## 4. Source index

Primary: `github.com/unclecode/crawl4ai` `deploy/docker/server.py` @ tags
0.6.3/0.7.5/0.8.0/main (0.9.3) · `docs.crawl4ai.com` markdown-generation &
api/parameters (0.9.x) · PyPI JSON API + sdists (chonkie 1.2.1→1.7.0,
markdown-it-py, mdformat, trafilatura 2.2.0, ftfy 6.3.1, fastmcp timeline) ·
`github.com/feyninc/chonkie` `src/chonkie/chef/markdown.py` ·
`github.com/executablebooks/markdown-it-py` + `mdformat` (+style.md,
validate) · `trafilatura.readthedocs.io/en/latest/evaluation.html`
(2026-08-04 table) · FineWeb arXiv 2406.17557 + HF blog + datatrove filter
sources (exact thresholds) · Gopher arXiv 2112.11446 Table A1 · C4 JMLR
20-074 · GneissWeb arXiv 2502.14907 · Kohlschütter et al. WSDM 2010 ·
jusText (Pomikálek 2011) · gofastmcp.com (tools.md, running-server, progress,
from-mcp-sdk-v1) · modelcontextprotocol.io spec 2025-06-18 (tools,
annotations) · docs.firecrawl.dev (onlyMainContent verified) ·
`github.com/jina-ai/reader` README.
