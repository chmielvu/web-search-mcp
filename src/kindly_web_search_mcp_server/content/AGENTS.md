# AGENTS.md - Content

Content acquisition, extraction, and conversion to LLM-ready Markdown.

## Current Pipeline

Two-tier architecture:
- **Tier 1 — Specialized resolvers** in `resolvers/`: StackExchange, GitHub Issues, GitHub Discussions, Wikipedia, arXiv, Telegram
- **Tier 2 — Generic extraction**: Jina Reader -> Crawl4AI `/md` (cloud) -> local BS4 (conditional) -> Camoufox last-resort

## Structure

```
content/
|-- artifact.py              # ContentArtifact / ContentError
|-- options.py               # FetchOptions
|-- windowing.py             # Content slicing and pagination
|-- status_classifier.py     # Content quality classification
|-- safe_fetch.py            # URL validation + HTTP fetch
|-- extract.py               # HTML extraction helpers
|-- sanitize.py              # Markdown cleanup
|-- html_tools.py            # Metadata / link parsing helpers
|-- remote_clients.py        # Crawl4AI `/md` + Camoufox `/content` HTTP clients
|-- tavily_map.py            # Tavily Map HTTP client
|-- stages.py                # 4 generic extraction stage functions
|-- fetch_pipeline.py        # Single-URL orchestrator (Tier 1 + Tier 2)
|-- specialized_pipeline.py  # Tier-1 resolver orchestration
|-- batch_orchestrator.py    # Batch fetch orchestration (per-URL only)
|-- sitemap.py               # Tavily-only sitemap orchestration
|-- link_discovery.py        # Link extraction from pages
|-- summary.py               # Gemini-backed URL/context summaries
|-- summary_backend.py       # Summary backend plumbing
|-- summary_models.py        # Summary models
|-- jina_reader.py           # Jina Reader client
|-- resolvers/               # 6 specialized resolvers
|   |-- stackexchange.py
|   |-- github_issues.py
|   |-- github_discussions.py
|   |-- wikipedia.py
|   |-- arxiv.py
|   └── telegram.py
```

## Current Behavior

- `fetch_pipeline.py` is the main single-URL path. Tier 1 (specialized domain resolvers) runs first; if no match, Tier 2 runs stages in order: Jina Reader -> Crawl4AI `/md` -> local BS4 (conditional) -> Camoufox last-resort.
- `batch_orchestrator.py` handles multi-URL fetches via per-URL `fetch_content_artifact` calls with semaphore-based concurrency and budget slicing.
- `sitemap.py` is Tavily-only sitemap discovery (no fallback).
- `CRAWL4AI_BASE_URL` enables remote Crawl4AI `/md` (cloud markdown, no browser).
- `CAMOUFOX_BASE_URL` enables the Camoufox stealth-Firefox sidecar as last-resort browser fallback.
- Local `crawl4ai` Python package and its transitive `playwright`/`playwright-stealth` deps have been removed.

## Adding a New Specialized Resolver

1. Add the resolver module in `content/resolvers/`
2. Wire the URL parsing and fetch stage into `specialized_pipeline.py`
3. Add tests that mock the upstream API or HTML payload

## Testing

- `python -m pytest tests/test_page_content_resolver.py`
- `python -m pytest tests/test_content_*.py tests/test_sitemap_orchestrator.py`
- `python -m pytest tests/test_remote_clients.py tests/test_stages.py`
