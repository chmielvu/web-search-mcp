"""VGI worker exposing unified web search to DuckDB/SQL.

Assembles the functions in ``vgi_search`` into a single ``search`` catalog and
runs the worker over stdio (a DuckDB subprocess) or HTTP (via serve.py).

Search is an **egress connector**: queries leave the engine for a third-party
search API. It is commodity access -- a thin wrapper whose value lives in the
upstream subscription, not the worker -- so it is built as AI-stack glue, framed
honestly (see README.md). The durable value is the pluggable-provider surface.

Usage:
    uv run search_worker.py              # serve over stdio (DuckDB subprocess)
    python serve.py --port 8000          # serve over HTTP

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'search' (TYPE vgi, LOCATION 'uv run search_worker.py');

    SELECT title, url, snippet
      FROM search.web_search('duckdb arrow protocol', provider := 'brave', count := 10);
    SELECT search.web_answer('who maintains duckdb', 'tavily');
    SELECT * FROM search.search_providers();

Provider API keys are supplied via the VGI **secret provider** (one secret type
per keyed provider: brave/tavily/exa/serpapi/serper); for local dev / CI they may
fall back to the ``<PROVIDER>_API_KEY`` env vars. Keys are NEVER passed in SQL.
"""

from __future__ import annotations

import json

from vgi import Worker
from vgi.catalog import Catalog, Schema, Table

from vgi_search.scalars import SCALAR_FUNCTIONS
from vgi_search.tables import PROVIDERS_TABLE_TAGS, TABLE_FUNCTIONS, SearchProviders

_CATALOG_DESCRIPTION_LLM = (
    "Run web searches from SQL through one pluggable provider surface (Brave, Tavily, Exa, "
    "SearXNG, DuckDuckGo, and the opt-in SerpApi/Serper SERP scrapers). Use it to retrieve live "
    "web results for RAG/retrieval: web_search(query, provider := ..., count := ..., page := ...) "
    "returns a unified row shape (title, url, snippet, rank, source, published, score, extra JSON) "
    "with provider-page pagination; web_answer(query, provider) returns a single synthesized "
    "one-line answer (Tavily or free DuckDuckGo Instant Answer) or NULL; search_providers() lists "
    "providers and which are configured. Provider API keys come from the VGI secret provider, "
    "never from SQL. This is an egress connector -- queries leave the engine for a third-party "
    "search API -- so results depend on the upstream subscription."
)

_CATALOG_DESCRIPTION_MD = (
    "# Unified Web Search in SQL\n\n"
    "Query the live web directly from DuckDB SQL through one pluggable provider surface that "
    "unifies **Brave Search**, **Tavily**, **Exa**, **SearXNG**, and **DuckDuckGo** behind a single "
    "result schema -- built for RAG, retrieval, and agent grounding.\n\n"
    "This extension turns web search into a first-class SQL data source. Instead of wiring a "
    "different HTTP client and JSON shape for every search API, you call one table function and get "
    "back the same unified rows -- `title`, `url`, `snippet`, `rank`, `source`, `published`, `score`, "
    "and an `extra` JSON column for provider-specific fields -- no matter which provider served the "
    "query. It is designed for developers building retrieval-augmented generation (RAG) pipelines, "
    "search-driven analytics, and AI agents that need fresh, citable web results inside the database, "
    "with the ability to swap or A/B providers without rewriting a single query.\n\n"
    "Under the hood the worker is a thin, dependency-light connector: every provider is reached over "
    "plain HTTP with the [httpx](https://www.python-httpx.org/) client "
    "([source](https://github.com/encode/httpx)) -- no heavyweight provider SDKs are bundled. Each "
    "backend is a small adapter that maps the upstream JSON into the shared result shape, with a "
    "per-call timeout plus bounded retry/backoff on rate-limit and server errors so a flaky provider "
    "never crashes the engine. Because it is an egress connector, results and quotas come from your "
    "own upstream subscription: API keys are supplied through the VGI secret provider (one secret "
    "type per keyed provider) and are **never** passed inline in SQL, while DuckDuckGo works key-free.\n\n"
    "The function surface is intentionally small. The table function "
    "`web_search(query, provider := ..., count := ..., page := ...)` runs one search against the "
    "chosen provider and streams the ranked, unified rows with 0-based page pagination. The scalar "
    "`web_answer(query, provider)` returns a single synthesized one-line answer (via Tavily or the "
    "free DuckDuckGo Instant Answer API) or NULL when none is available. The table function "
    "`search_providers()` lists every provider and whether each currently has a key or base URL "
    "configured. Runnable, catalog-qualified queries for each function live in that object's "
    "examples.\n\n"
    "### Providers & documentation\n\n"
    "- **Brave Search** -- [API docs](https://brave.com/search/api/), "
    "[developer dashboard](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)\n"
    "- **Tavily** -- [site](https://tavily.com/), [docs](https://docs.tavily.com/)\n"
    "- **Exa** -- [site](https://exa.ai/), [docs](https://docs.exa.ai/)\n"
    "- **SearXNG** (self-hosted, base_url required) -- "
    "[source](https://github.com/searxng/searxng), [docs](https://docs.searxng.org/)\n"
    "- **DuckDuckGo** (free, key-free) -- [duckduckgo.com](https://duckduckgo.com/)\n\n"
    "Opt-in SerpApi/Serper SERP backends are also available when explicitly enabled. Built and "
    "maintained by [Query.Farm](https://query.farm)."
)

_SCHEMA_DESCRIPTION_LLM = (
    "Web-search functions over a pluggable provider surface.\n\n"
    "- `web_search(query, provider := ..., count := ..., page := ...)` -- table function returning "
    "ranked results in a unified schema (title, url, snippet, rank, source, published, score, "
    "extra JSON).\n"
    "- `web_answer(query, provider)` -- scalar returning a synthesized one-line answer, or NULL.\n"
    "- `search_providers()` -- table function listing the providers and which are configured.\n\n"
    "Backed by Brave, Tavily, Exa, SearXNG, DuckDuckGo, and the opt-in SerpApi/Serper scrapers. "
    "Provider keys come from the VGI secret provider, never from SQL; `ddg` works for free. Use "
    "these for RAG / retrieval that needs live web results."
)

_SCHEMA_DESCRIPTION_MD = (
    "# search.main\n\n"
    "Web-search functions over a **pluggable provider surface**, for RAG / retrieval.\n\n"
    "- `web_search` -- ranked results as a table (unified schema across providers).\n"
    "- `web_answer` -- a synthesized one-line answer (scalar), or NULL.\n"
    "- `search_providers` -- provider discovery (names, capabilities, configured state).\n\n"
    "Providers: Brave, Tavily, Exa, SearXNG, DuckDuckGo (free), plus opt-in SerpApi/Serper. "
    "Keys are supplied via the VGI secret provider, never inline in SQL."
)

# VGI515/VGI506: schema-level illustrative examples as a described JSON list
# ({"description","sql"}), catalog-qualified so they run against the attached
# worker (VGI505).
_SCHEMA_EXAMPLE_QUERIES = json.dumps(
    [
        {
            "description": "List every provider and whether each is configured, before choosing one.",
            "sql": "SELECT provider, requires_key, configured FROM search.main.search_providers() ORDER BY provider",
        },
        {
            "description": "Top web results via the free, keyless DuckDuckGo provider.",
            "sql": (
                "SELECT title, url, rank FROM "
                "search.main.web_search('python programming language', provider := 'ddg', count := 5) "
                "ORDER BY rank"
            ),
        },
        {
            "description": "Top-10 web results via Brave (needs a key).",
            "sql": (
                "SELECT title, url, snippet FROM "
                "search.main.web_search('vector database', provider := 'brave', count := 10)"
            ),
        },
        {
            "description": "A synthesized one-line answer via free DuckDuckGo Instant Answer.",
            "sql": "SELECT search.main.web_answer('python programming language', 'ddg') AS answer",
        },
    ]
)

# VGI413: the schema's category registry. Each object declares a `vgi.category`
# naming one of these; categories drive navigation / listing sections / SEO.
_SCHEMA_CATEGORIES = json.dumps(
    [
        {
            "name": "Web Search",
            "description": (
                "Run live web searches and synthesized answers through the pluggable "
                "provider surface (web_search, web_answer)."
            ),
        },
        {
            "name": "Provider Discovery",
            "description": (
                "Inspect which search providers exist and whether each is configured, "
                "before choosing a provider (search_providers)."
            ),
        },
    ]
)

# VGI152: an analyst-task suite so `vgi-lint simulate` can measure how well an
# agent actually uses this worker. Every reference query uses the keyless `ddg`
# provider or the backend-free `search_providers()` directory, so the suite runs
# without any API key or paid subscription.
_AGENT_TEST_TASKS = json.dumps(
    [
        {
            "name": "provider-directory",
            "prompt": (
                "Show me the full directory of search providers this worker supports: every provider, "
                "with all of its columns, ordered by provider name."
            ),
            "reference_sql": "SELECT * FROM search.main.search_providers() ORDER BY provider",
        },
        {
            "name": "keyed-providers",
            "prompt": "Which of the search providers require an API key? List just their names, sorted.",
            "reference_sql": (
                "SELECT provider FROM search.main.search_providers() WHERE requires_key ORDER BY provider"
            ),
        },
        {
            "name": "answer-capable-providers",
            "prompt": "Which providers can return a synthesized one-line answer for web_answer? List their names, sorted.",
            "reference_sql": (
                "SELECT provider FROM search.main.search_providers() WHERE supports_answer ORDER BY provider"
            ),
        },
        {
            "name": "ready-to-use-providers",
            "prompt": "List the names of the search providers that are configured and ready to use right now, sorted.",
            "reference_sql": ("SELECT provider FROM search.main.search_providers() WHERE configured ORDER BY provider"),
        },
        {
            "name": "free-web-search",
            "prompt": (
                "Using the free, keyless DuckDuckGo (ddg) provider, search for 'python programming "
                "language' and return the titles and URLs of the top 5 results ordered by rank."
            ),
            "reference_sql": (
                "SELECT title, url FROM "
                "search.main.web_search('python programming language', provider := 'ddg', count := 5) "
                "ORDER BY rank LIMIT 5"
            ),
        },
        {
            "name": "keyless-one-line-answer",
            "prompt": (
                "Get a single synthesized one-line answer for 'python programming language' using the "
                "free DuckDuckGo (ddg) provider, without any API key."
            ),
            "reference_sql": "SELECT search.main.web_answer('python programming language', 'ddg') AS answer",
        },
    ]
)

_SEARCH_CATALOG = Catalog(
    name="search",
    default_schema="main",
    comment="Unified web search over pluggable providers for SQL / RAG.",
    source_url="https://github.com/Query-farm/vgi-search",
    tags={
        "vgi.title": "Unified Web Search",
        "vgi.keywords": json.dumps(
            [
                "web search",
                "search",
                "retrieval",
                "rag",
                "serp",
                "results",
                "brave",
                "tavily",
                "exa",
                "searxng",
                "duckduckgo",
                "ddg",
                "serpapi",
                "serper",
                "web answer",
                "providers",
            ]
        ),
        "vgi.doc_llm": _CATALOG_DESCRIPTION_LLM,
        "vgi.doc_md": _CATALOG_DESCRIPTION_MD,
        "vgi.author": "Query.Farm",
        "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
        "vgi.license": "MIT",
        "vgi.support_contact": "https://github.com/Query-farm/vgi-search/issues",
        "vgi.support_policy_url": "https://github.com/Query-farm/vgi-search/blob/main/README.md",
        "vgi.agent_test_tasks": _AGENT_TEST_TASKS,
    },
    schemas=[
        Schema(
            name="main",
            comment="Unified web search over pluggable providers for SQL / RAG",
            tags={
                "vgi.title": "Web Search — main",
                "vgi.keywords": json.dumps(
                    [
                        "web search",
                        "web_search",
                        "web_answer",
                        "search_providers",
                        "retrieval",
                        "rag",
                        "serp",
                        "results",
                        "providers",
                        "brave",
                        "tavily",
                        "exa",
                        "searxng",
                        "duckduckgo",
                        "ddg",
                    ]
                ),
                # VGI123 classifying tags (BARE keys: domain/category/topic) for faceting.
                "domain": "information-retrieval",
                "category": "web-search",
                "topic": "search-providers",
                "vgi.doc_llm": _SCHEMA_DESCRIPTION_LLM,
                "vgi.doc_md": _SCHEMA_DESCRIPTION_MD,
                "vgi.example_queries": _SCHEMA_EXAMPLE_QUERIES,
                "vgi.categories": _SCHEMA_CATEGORIES,
            },
            functions=[*SCALAR_FUNCTIONS, *TABLE_FUNCTIONS],
            # `search_providers()` is parameterless and always returns the same
            # provider directory, so also expose it as a regular table backed by
            # the same generator (VGI311): `SELECT * FROM search.main.search_providers`.
            tables=[
                Table(
                    name="search_providers",
                    function=SearchProviders,
                    comment="Directory of available search providers and whether each is configured",
                    # Every provider row is fully populated, and the provider name
                    # uniquely identifies a row (VGI806/VGI807 constraints).
                    primary_key=(("provider",),),
                    not_null=("provider", "requires_key", "supports_answer", "configured"),
                    tags=PROVIDERS_TABLE_TAGS,
                ),
            ],
        ),
    ],
)


class SearchWorker(Worker):
    """Worker process hosting the ``search`` catalog."""

    catalog = _SEARCH_CATALOG


def main() -> None:
    """Run the search worker process (stdio or, via flags, HTTP)."""
    SearchWorker.main()


if __name__ == "__main__":
    main()
