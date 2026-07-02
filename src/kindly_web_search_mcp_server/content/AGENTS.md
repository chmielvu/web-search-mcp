# AGENTS.md - Content

Content acquisition, extraction, and conversion to LLM-ready Markdown.

## Current Pipeline

- Specialized resolvers first: `stackexchange.py`, `github_issues.py`,
  `github_discussions.py`, `wikipedia.py`, `arxiv.py`
- Crawl4AI remote second: `crawl4ai_client.py`, `fetch_pipeline.py`,
  `batch_orchestrator.py`, `sitemap.py`
- Fallback last: `fallback.py` (Jina Reader -> trafilatura)

## Structure

```
content/
|-- artifact.py              # ContentArtifact / ContentError
|-- options.py               # FetchOptions
|-- windowing.py             # Content slicing and pagination
|-- status_classifier.py     # Content quality classification
|-- safe_fetch.py            # URL validation + HTTP fetch
|-- link_discovery.py        # Link extraction from pages
|-- summary.py               # Gemini-backed URL/context summaries
|-- summary_backend.py       # Summary backend plumbing
|-- summary_models.py        # Summary models
|-- extract.py               # Trafilatura extraction helpers
|-- sanitize.py              # Markdown cleanup
|-- html_tools.py            # Metadata / link parsing helpers
|-- crawl4ai_client.py       # Remote Crawl4AI HTTP client
|-- fetch_pipeline.py        # Single-URL orchestrator
|-- batch_orchestrator.py    # Batch fetch orchestration
|-- sitemap.py               # Crawl4AI semantic sitemap extraction
|-- fallback.py              # Jina Reader -> trafilatura fallback chain
|-- stackexchange.py         # StackExchange resolver
|-- github_issues.py         # GitHub Issues resolver
|-- github_discussions.py    # GitHub Discussions resolver
|-- wikipedia.py             # Wikipedia resolver
└── arxiv.py                 # arXiv resolver
```

## Current Behavior

- `fetch_pipeline.py` is the main single-URL path.
- `batch_orchestrator.py` handles multi-URL fetches and batch budgets.
- `sitemap.py` is the Crawl4AI deep-crawl path and can generate llms.txt
  output.
- `CRAWL4AI_BASE_URL` enables remote Crawl4AI; otherwise the stack falls back
  to Jina Reader and then trafilatura.

## Adding a New Specialized Resolver

1. Add the resolver module in `content/`
2. Wire the URL parsing and fetch stage into `fetch_pipeline.py`
3. Add tests that mock the upstream API or HTML payload

## Testing

- `python -m pytest tests/test_page_content_resolver.py`
- `python -m pytest tests/test_content_*.py tests/test_sitemap.py`
