<p align="center">
  <img src="https://raw.githubusercontent.com/Query-farm/vgi/main/docs/vgi-logo.png" alt="Vector Gateway Interface (VGI)" width="320">
</p>

<p align="center"><em>A <a href="https://query.farm">Query.Farm</a> VGI worker for DuckDB.</em></p>

# Unified Web Search (Brave, Tavily, Exa, DDG, SearXNG) in DuckDB

> **vgi-search** · a [Query.Farm](https://query.farm) VGI worker

Unified **web search** as DuckDB SQL functions — for retrieval-augmented
generation (RAG) and ad-hoc lookups — behind **one pluggable provider surface**.

A [VGI](https://query.farm) worker that lets you query the web from SQL and get
back a single normalized result schema regardless of which search API served it.
It completes the AI/RAG retrieval stack alongside
[`vgi-embed`](https://query.farm) (vectors) and a reranker (precision):
**search → embed → rerank → feed an LLM.**

```sql
INSTALL vgi FROM community; LOAD vgi;
ATTACH 'search' (TYPE vgi, LOCATION 'uv run search_worker.py');

-- Top-N web search via the configured/default provider.
SELECT title, url, snippet, rank, source, published
FROM search.web_search('duckdb arrow protocol', provider := 'brave', count := 10);

-- RAG composition: search the web, then rank by your own embedding similarity.
WITH hits AS (SELECT * FROM search.web_search('vector databases', provider := 'tavily', count := 20))
SELECT url, snippet FROM hits;  -- ... ORDER BY rerank_score(:q, snippet) DESC LIMIT 5;

-- A free, no-key quick fact (DuckDuckGo Instant Answer — zero-click box).
SELECT search.web_answer('python programming language', 'ddg');

-- Which providers are available and configured?
SELECT * FROM search.search_providers() ORDER BY provider;
```

## Honest framing (read this)

This worker is an **egress connector**: your queries leave the engine and go to a
third-party search API. If you have data-residency constraints, note that.

It is **commodity access** — a thin, well-built wrapper over search APIs whose
value and cost live in the **upstream subscription**, not in this code. The
pagination here is a trivial page/offset integer — the *easy* kind of scan
state, not the hard, stateful kind (Kafka offsets, CDC LSNs) that makes a
connector defensible. So vgi-search is **AI-stack glue + breadth, not a moat.**

The one durable piece of value is the **pluggable-provider design**: a single SQL
surface over interchangeable backends. It future-proofs you against any single
API dying — as the Bing Web Search API did when Microsoft retired it in 2025. Add
or swap a provider; your SQL doesn't change.

## The unified result schema

Every provider is normalized to one row shape, so your SQL is provider-agnostic:

| column | type | notes |
|---|---|---|
| `title` | `VARCHAR` | result title |
| `url` | `VARCHAR` | result URL |
| `snippet` | `VARCHAR` | description / excerpt |
| `rank` | `INTEGER` | 1-based position in the result set (assigned by the worker, consistent across providers) |
| `source` | `VARCHAR` | provider name (e.g. `brave`) |
| `published` | `TIMESTAMPTZ` | publication time when the provider exposes one, else NULL |
| `score` | `DOUBLE` | provider relevance score when available, else NULL |
| `extra` | `VARCHAR` (JSON) | provider-specific fields, JSON-encoded — reach in with `->>` / `json_extract` |

Missing fields normalize to `NULL`; nothing crashes on a thin or odd response.

## Functions

| function | kind | signature |
|---|---|---|
| `web_search(query, provider, count, page)` | table | `provider` / `count` / `page` are **named args** (`provider := 'brave'`); returns the unified schema |
| `web_answer(query, provider)` | scalar | **positional-only** (`'tavily'` / `'ddg'`); a synthesized answer string, or `NULL` |
| `search_providers()` | table | `(provider, requires_key, supports_answer, configured)` |

`web_search` is a **table function**, so it takes DuckDB `name := value` named
arguments. `web_answer` is a **scalar**, which in VGI/DuckDB is **positional-only**
(`name := value` is a table-function feature), so its `provider` is a positional
constant. `count` defaults to 10; the default provider is `brave` (override with
the `VGI_SEARCH_DEFAULT_PROVIDER` env var). The page argument is named **`page`**
(a 0-based page index), **not** `offset` — `offset` is a DuckDB reserved keyword,
so `offset := 1` is a parser error.

## Providers (v1)

| provider | key? | answer? | what it is | ToS note |
|---|---|---|---|---|
| **brave** | yes | no | Brave Search API — an **independent** web index (not a Google/Bing reseller). The clean general default. | Direct API use per Brave's terms; bring your own key. |
| **tavily** | yes | **yes** | LLM/RAG-optimized search + a synthesized `answer`. The AI-native pick. | Direct API; your subscription. |
| **exa** | yes | no | Neural/embeddings web search for AI; content + highlights. | Direct API; your subscription. |
| **ddg** | **no** | **yes** | DuckDuckGo **Instant Answer** API — **zero-click answers, NOT a web SERP.** Most ordinary queries return empty. Free quick-fact path only. | Official documented endpoint; no scraping. |
| **searxng** | no (`base_url`) | no | Query your **self-hosted** [SearXNG](https://docs.searxng.org/) metasearch instance. `base_url` is required. | ToS shifts to **you**, the instance operator. |

Flagged / off by default (set `VGI_SEARCH_ENABLE_SERP=1` to opt into the risk):

| provider | what it is | ToS note |
|---|---|---|
| **serpapi** | Google/Bing SERP via the paid [SerpApi](https://serpapi.com/) scraping service | SERP-scraping legality is contested; that risk is **the operator's**. |
| **serper** | Google SERP via the paid [serper.dev](https://serper.dev/) API | Same framing as SerpApi. |

**We never scrape Google/Bing/DuckDuckGo HTML directly** — it's fragile and a ToS
violation. The flagged providers delegate that to services whose business is
exactly that, and whose terms you accept by enabling them. **We do not include
the Bing Web Search API — Microsoft retired it in 2025.**

> `ddg` is the only provider that returns *Instant Answers* (a curated definition
> or abstract), **not** a ranked web SERP. Use it for quick facts, not breadth.

## Authentication — the secret provider

Per-provider API keys are supplied through the VGI **secret provider**, never
inline in SQL. The worker declares one secret type per keyed provider
(`brave`, `tavily`, `exa`, `serpapi`, `serper`); the framework resolves them and
hands them to the function at query time. Keys are read from the conventional
field names (`api_key` / `key` / `token` / `value`) and are never logged.

For **local development and CI** (and the mock-server tests), keys may instead
come from environment variables — `BRAVE_API_KEY`, `TAVILY_API_KEY`,
`EXA_API_KEY`, etc. — and a SearXNG instance from `VGI_SEARCH_SEARXNG_BASE_URL`.
The secret provider is the production path; the env vars are the dev fallback.

`search_providers()` shows which backends are configured **without** revealing
any secret value.

## Pagination

`page` selects a provider page (`count` results per page); the worker streams
that page to DuckDB and carries the page cursor as plain-serializable
**scan state**, round-tripped across batch boundaries. Providers with native
paging (Brave, SearXNG, the SERP services) map directly; those without (Tavily,
Exa, DDG) are paged client-side by over-fetch-and-slice. This is the trivial,
documented, non-defensible kind of scan state.

## Reliability

Every provider call has a **per-call timeout** and a **bounded retry with
exponential backoff** on 429 / 5xx. A provider error (bad key, HTTP failure,
timeout) surfaces as a clean DuckDB error and **never crashes the worker**;
`web_answer` degrades to `NULL` rather than failing a scan.

## Development

```bash
uv sync --extra dev
uv run --no-sync pytest -q -m "not live"   # fixtures + mock-server E2E (no keys, no network)
uv run --no-sync pytest -q -m live         # optional: real DDG Instant Answer (free, needs network)
make test-sql                              # haybarn-unittest E2E over test/sql/* (authoritative)
make test                                  # both
uv run --no-sync ruff check . && uv run --no-sync mypy vgi_search/
```

The SQL E2E suite points every provider's `base_url` at a local mock HTTP server,
so it is deterministic and needs no keys or real network egress.

## Roadmap / non-goals

- **Non-goals:** image/video/maps verticals; direct Google/Bing scraping; a
  result-caching layer.
- **Roadmap:** more providers (Kagi, Mojeek); and separate **clean vertical**
  workers with rich domain schemas rather than generic SERP rows — `vgi-scholar`
  (OpenAlex / Crossref / arXiv / PubMed), `vgi-wikipedia`, `vgi-news` (GDELT).
  Those sources are free and ToS-clean and deserve their own schemas.

## License

Worker code: **MIT** (see [LICENSE](LICENSE)). The one runtime dependency,
[`httpx`](https://www.python-httpx.org/), is BSD-3-Clause; `pyarrow` is
Apache-2.0. The `vgi` DuckDB extension and `vgi-python` are licensed separately
by Query Farm. Each search provider's API has its **own** terms of service, and
the keys / subscriptions are **your** responsibility.

---

## Authorship & License

Written by [Query.Farm](https://query.farm).

Copyright 2026 Query Farm LLC - https://query.farm

