<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-02 | Last verified: 2026-09-02 -->

# AGENTS.md - Content

Content acquisition, extraction, and conversion to LLM-ready Markdown.

## Pipeline Architecture

Three-tier architecture with active resilience:

- **Tier 1 — Specialized Resolvers** (`resolvers/`):
  - Documents: PDF (PyMuPDF), Office/EPUB (MarkItDown), Jupyter Notebooks (.ipynb AST), CSV/TSV tables, MHTML, Google Docs/Sheets URL rewriting
  - Structured text: JSON/JSONL, YAML, TOML, XML, RSS/Atom
  - Rich text and media text: RTF, VTT/SRT subtitles, safe SVG text extraction
  - Columnar data: bounded Parquet, Arrow IPC, and Feather schema/sample rendering
  - Raw Text & Source Code files
  - Academic DOIs & Open Access papers (Unpaywall / Crossref)
  - Package Registries (PyPI, npm, Hugging Face Hub, Crates.io)
  - Developer & Q&A platforms (StackExchange, GitHub Issues/PRs/Discussions/Repos, Discourse, HackerNews, Reddit with 3 free layers + optional Apify Layer 4, X/Twitter via optional Apify, Wikipedia, arXiv, YouTube, Telegram)
- **Tier 2 — Generic Extraction Cascade**:
  1. Jina Reader (cloud markdown, research preset)
  2. Local Extraction (`curl_cffi` Chrome 124 JA3/JA4 TLS impersonation + Trafilatura / BS4 / Document converters)
  3. Crawl4AI `/md` (remote cloud markdown, skipped for binary targets)
  4. Camoufox Sidecar (stealth Firefox browser on port 3000 for SPAs and hard sites)
- **Tier 3 — Web Archive Resilience Fallback**:
  Internet Archive Wayback Machine Availability API (`resolvers/wayback.py`) for 404, 410, or persistent blocked sites.

## Key Files

| File | Role |
|---|---|
| `fetch_pipeline.py` | Single-URL orchestrator (Tier 1 + Tier 2 cascading) & `FetchOptions` |
| `specialized_pipeline.py` | Tier 1 resolver routing |
| `stages.py` | 4 generic extraction stage functions |
| `artifact.py` | `ContentArtifact` / `ContentError` models |
| `format_renderers.py` | Bounded structured, subtitle, SVG, MHTML, and columnar Markdown renderers |
| `html_extract.py` | HTML extraction ladder: Trafilatura → BS4+markdownify → regex fallback, chrome pruning. Each rung returns `sanitize_markdown` only (from `utils/text_clean.py`). |
| — | Text hygiene and status classification live in `utils/text_clean.py` (markdown cleaning, Jina frontmatter) and `utils/content_classify.py` (`classify_markdown`, `chrome_ratio`, `wall_from_classification`). |
| — | Content slicing/pagination lives in `utils/text_chunking.py` (`slice_content`, `ContentWindow`, `WindowedContent`) since the windowing module merge. |
| `ai_summary.py` | Unified LLM summarization pipeline, models, and fallback ladder |
| `safe_fetch.py` | Safe HTTP fetch wrapper with content-type validation |
| `jina_reader.py` | Jina Reader HTTP client |
| `remote_clients.py` | Crawl4AI + Camoufox + optional Apify HTTP clients |
| `link_discovery.py` | Link extraction from pages |
| `tavily_map.py` | Tavily-only sitemap generation (`map_site`) |
| `resolvers/` | Specialized URL resolvers (Documents, PyPI, npm, HuggingFace, Crates.io, Unpaywall/DOI, Discourse, Reddit, X/Twitter, GitHub, StackExchange, Wikipedia, arXiv, YouTube, Telegram, Wayback) |

## Rules

- `fetch_pipeline.py` is the main single-URL path. Tier 1 runs first; if no
  match, Tier 2 runs stages in order.
- Specialized parsers either raise for a non-match or return a target; routing
  must treat an explicit `None` return as no match so generic extraction can run.
- The unified `fetch` tool in `tools/content.py` handles one or many URLs
  through the same core. Bulk inputs admit one `web_fetch_wave_size` wave per
  call; callers use `cursor` for remaining URLs. There is no fetch markdown
  char cap; `offset` paginates a single URL. `FetchResponse.duration_ms` is
  first-class.
- `tavily_map.py` is Tavily-only (no fallback).
- Per-stage timeouts: Jina 25s, Crawl4AI 30s, local 20s, Camoufox 35s.
- Jina Reader circuit breaker: opens after 3 failures in 60s. Jina markdown is classified on the pre-strip body (chrome ratio) and its frontmatter is parsed for fields (`title/url/warning`); a non-empty `warning` field maps to `blocked` + `access_blocked:jina_warning`. `X-Retain-Links` is `all`.
- `sanitize.py` cleaning is fence-aware: fenced/indented code, nested lists, and tables are preserved verbatim; classification uses additive phrase scores (0.25 generic, 0.5 strong) with explicit HTTP-status precedence (401 → login, 403/429 → blocked, 404/410/5xx → error) and a long-doc veto (>150 words) that bypasses phrase wins.
- Content-type validation routes HTML, JSON/JSONL, YAML, TOML, RSS/Atom, CSV/TSV, XML, RTF, subtitles, SVG, plain text, Office, MHTML, and columnar documents without browser escalation. Jina JSON/XML envelopes are relabeled via `relabel_typed_artifact`.
- Optional summaries use the Gemini chain `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite` → Gemma; `fetch` exposes `ai_summary: bool = false`. Non-empty fetched content disables URL-context; empty body + URLs still uses it.

## Adding a New Specialized Resolver

1. Add the resolver module in `content/resolvers/`
2. Wire URL parsing and fetch stage into `specialized_pipeline.py`
3. Add tests mocking the upstream API or HTML payload

## Testing

```bash
uv run pytest tests/test_page_content_resolver.py
uv run pytest tests/test_content_*.py tests/test_sitemap_orchestrator.py
uv run pytest tests/test_remote_clients.py tests/test_stages.py
```
### Recent Changes (2026-09-06)
- The unified fetch path keeps metadata and format details internal to artifacts,
  cache envelopes, and analytics. Public results expose `content` plus the
  pagination window; continuation notices are no longer part of window models.
