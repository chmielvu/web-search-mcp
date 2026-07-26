# AGENTS.md - Content

Content acquisition, extraction, and conversion to LLM-ready Markdown.

## Pipeline Architecture

Two-tier architecture:

- **Tier 1 — Specialized resolvers** (`resolvers/`): StackExchange, GitHub Issues,
  GitHub Discussions, Wikipedia, arXiv, Telegram, YouTube
- **Tier 2 — Generic extraction**: Jina Reader → Crawl4AI `/md` (cloud) → local
  BS4 (conditional) → Camoufox last-resort

## Key Files

| File | Role |
|---|---|
| `fetch_pipeline.py` | Single-URL orchestrator (Tier 1 + Tier 2 cascading) |
| `specialized_pipeline.py` | Tier 1 resolver routing |
| `batch_orchestrator.py` | Multi-URL fetch with semaphore concurrency |
| `stages.py` | 4 generic extraction stage functions |
| `artifact.py` | `ContentArtifact` / `ContentError` models |
| `options.py` | `FetchOptions` |
| `windowing.py` | Content slicing with pagination (`has_more`, `next_offset`) |
| `status_classifier.py` | Content quality classification |
| `summary.py` | Gemini-backed URL/context summaries |
| `jina_reader.py` | Jina Reader HTTP client |
| `remote_clients.py` | Crawl4AI + Camoufox HTTP clients |
| `link_discovery.py` | Link extraction from pages |
| `sitemap.py` | Tavily-only sitemap generation |
| `resolvers/` | 6 specialized URL resolvers |

## Rules

- `fetch_pipeline.py` is the main single-URL path. Tier 1 runs first; if no
  match, Tier 2 runs stages in order.
- Specialized parsers either raise for a non-match or return a target; routing
  must treat an explicit `None` return as no match so generic extraction can run.
- `batch_orchestrator.py` handles multi-URL fetches per-URL with semaphore
  concurrency and budget slicing.
- `sitemap.py` is Tavily-only (no fallback).
- Page cache pre-check runs inside `run_batch_fetch` for reuse.
- Per-stage timeouts: Jina 25s, Crawl4AI 30s, local 20s, Camoufox 35s.
- Jina Reader circuit breaker: opens after 3 failures in 60s.
- Content-type validation rejects non-HTML/XML/plain responses.
- Optional summaries use the Gemini chain `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite` → Gemma; batch summaries retry the two Gemini tiers before per-item fallback. The public `get_content` and `batch_get_content` contract is `ai_summary: bool = false`; `true` selects the detailed summary only.

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
