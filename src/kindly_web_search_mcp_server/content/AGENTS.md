# AGENTS.md - Content

Content acquisition, extraction, and conversion to LLM-ready Markdown.

## Architecture

Two-tier pipeline:
- **Tier 1:** Specialized resolvers (StackExchange, GitHub Issues/Discussions, Wikipedia, arXiv)
- **Tier 2:** Crawl4AI remote (primary) → fallback (Jina Reader → trafilatura)

## Structure

```
content/
├── crawl4ai_client.py       # Remote HTTP client for Crawl4AI Docker API
├── fallback.py              # Jina Reader → trafilatura fallback chain
├── fetch_pipeline.py        # Main pipeline orchestrator (2-tier)
├── batch_orchestrator.py    # Batch fetch with Crawl4AI batch mode
├── sitemap.py               # Semantic sitemap via Crawl4AI deep crawl
│
├── extract.py               # Trafilatura two-pass extraction
├── sanitize.py              # Markdown cleanup
├── html_tools.py            # BeautifulSoup (metadata, links, sitemap XML)
│
├── artifact.py              # ContentArtifact, ContentError
├── options.py               # FetchOptions
├── windowing.py             # Content windowing/pagination
├── status_classifier.py     # Content quality classification
│
├── safe_fetch.py            # URL validation + HTTP fetch
├── jina_reader.py           # Jina Reader client
├── link_discovery.py        # Link extraction from pages
│
├── summary.py               # Gemini URL-context summaries
├── summary_backend.py       # Summary LLM backend
├── summary_models.py        # Summary data models
│
├── stackexchange.py         # StackExchange API resolver
├── github_issues.py         # GitHub Issues GraphQL resolver
├── github_discussions.py    # GitHub Discussions GraphQL resolver
├── wikipedia.py             # Wikipedia MediaWiki API resolver
└── arxiv.py                 # arXiv Atom API + PDF resolver
```

## Pipeline Flow

1. **StackExchange** — full thread (Q + A + comments) for SO/SE sites
2. **GitHub Issues** — GraphQL-based issue extraction
3. **GitHub Discussions** — GraphQL-based discussion extraction
4. **Wikipedia** — MediaWiki Action API
5. **arXiv** — Atom API + PDF → Markdown
6. **Crawl4AI remote** — `POST /crawl` → fit_markdown + html + links
7. **Fallback** — Jina Reader (free) → trafilatura (offline)

## Adding a New Specialized Resolver

1. Create module in `content/` with `parse_x_url()` and `fetch_x_markdown()`
2. Add import and handler stage in `fetch_pipeline.py`
3. Write unit tests in `tests/test_x.py` mocking the API

## Key Settings

- `CRAWL4AI_BASE_URL` — remote Crawl4AI server URL (enables Tier 2 primary)
- `CRAWL4AI_TIMEOUT_SECONDS` — request timeout (default 120s)

## Testing
```
pytest tests/test_page_content_resolver.py -v
```
