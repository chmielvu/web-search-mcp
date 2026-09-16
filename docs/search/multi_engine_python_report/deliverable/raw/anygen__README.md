# anygen-search-cli (`hsearch`)

> **Unified CLI over 6 commercial search APIs** — Brave · Serper · Exa · Tavily · Firecrawl · Jina.
> One command, six providers, dedup'd results, Perplexity-style answers, full-content extraction.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-245%20passing-brightgreen.svg)](tests/)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](CHANGELOG.md)

---

## Why this exists

Every search provider has unique strengths — Tavily writes great answers, Exa
nails neural/semantic recall, Serper has cheap Google SERPs, Jina turns any
URL into clean markdown for free. **Wiring them up one-by-one in every agent
or script is wasteful.**

`hsearch` is a single fast CLI that:

- 🔑 **Auto-detects** which provider keys you've set; ignores the rest.
- 🔀 **Routes** queries by mode (`news` / `academic` / `realtime` / `answer` / …) to the best-suited provider.
- 🌐 **Parallel `--all`** — query every configured provider concurrently and dedup by URL.
- 🎯 **High-recall `--mode recall`** — Exa deep reasoning + Tavily advanced chunks + Brave LLM Context + Firecrawl web/news content.
- 🤖 **Perplexity-style** `--mode answer` — synthesized answer panel + sources.
- 📄 **Per-result summaries** (`--summary`) and **raw markdown** (`--raw`).
- 🆕 **Fresh Exa content** (`--max-age-hours 0`), **time windows** (`--days 7`, `--time week`), **site filters**.
- 💾 **Smart disk cache** — adaptive TTL by mode, overridable per call.
- 📦 **5 output formats**: `table` · `json` · `jsonl` · `markdown` · `urls`.
- 🧪 **113 mocked tests**, no real keys needed to run.

---

## Install

```bash
pip install git+https://github.com/AnyGenIO/anygen-search-cli.git
```

Or from source:

```bash
git clone https://github.com/AnyGenIO/anygen-search-cli.git
cd anygen-search-cli
pip install -e .
```

> Requires Python **3.9+**. Dependencies: `httpx`, `typer`, `rich`,
> `python-dotenv`, `diskcache`.

### Configure API keys

Set at least one provider's API key as an environment variable:

```bash
# Add to ~/.bashrc, ~/.zshrc, or equivalent
export TAVILY_API_KEY="tvly-xxx"
export BRAVE_API_KEY="BSAxxxxx"
export SERPER_API_KEY="xxx"
export EXA_API_KEY="xxx"
export FIRECRAWL_API_KEY="fc-xxx"
export JINA_API_KEY="jina_xxx"
```

Alternatively, put them in a `.env` file (project-local `./. env`, or `~/.hermes/.env`).

Verify:

```bash
python3 -m hsearch providers
```

---

## LLM / Agent Integration

`hsearch` is designed to be called by LLM agents via shell. Two features make this work:

### 1. `--agent` flag

Always outputs structured JSON with sensible defaults (`--format json --top 5`):

```bash
python3 -m hsearch search "your query" --agent
```

### 2. `hsearch schema` — LLM self-discovery

```bash
python3 -m hsearch schema
```

Outputs a complete JSON tool definition (parameters, output schema, examples, tips). An LLM agent can run this once to learn how to use `hsearch` — no manual docs needed.

### 3. `hsearch mcp` — native MCP server

Install the optional extra and point MCP-aware clients at the stdio server:

```bash
pip install -e ".[mcp]"
hsearch mcp
```

See [docs/MCP.md](docs/MCP.md) for Claude Desktop and Codex config examples.

### 4. Python SDK

```python
from hsearch import search_sync, SearchResult

resp = search_sync("Python tutorial", mode="general", top=5)
for r in resp.results:
    print(r.title, r.url)

# Or with explicit API keys (no env vars needed)
resp = search_sync("query", api_keys={"tavily": "tvly-xxx"})
```

Async:

```python
from hsearch import search
resp = await search("query", providers=["tavily", "brave"], top=5)
```

### Add to Claude Code / Cursor / AI IDE

Add one line to your `CLAUDE.md` or `.cursorrules`:

```
You have access to `hsearch` for web search. Run `python3 -m hsearch schema` to learn usage.
```

That's it — the LLM reads the schema and figures out the rest.

---

## Quick recipes

```bash
# NEW v1.0.0 — ONE pre-assembled LLM-ready context string for grounding (Exa contents.context)
hsearch search "how does RAG chunking work" --mode rag --top 5 --format json | jq -r .meta.context
hsearch search "MCP spec" --context --context-max-chars 4000 --top 3   # bound it (unbounded ≈168K chars)

# NEW v1.0.0 — stream a deep-research report as it is written (SSE)
hsearch research "compare Postgres vs SQLite for edge apps" --model mini --stream

# NEW v1.0.0 — remaining quota before an expensive sweep
hsearch usage

# NEW v0.9.0 — map a site's COMPLETE URL inventory (beats React pagination / stale sitemaps)
hsearch map "https://docs.tavily.com" --limit 200 -f urls

# NEW v0.9.0 — crawl + extract, with natural-language traversal steering
hsearch crawl "https://docs.firecrawl.dev" --limit 20 --instructions "API reference endpoint pages only"

# NEW v0.9.0 — Exa `people` category: LinkedIn/profile lookup
hsearch search "Jensen Huang" --provider exa --category people --top 5

# NEW v0.8.0 — Exa Agent: async deep-research / list-building / enrichment agent
hsearch agent "What is Rocket Lab's ticker and business?" --effort minimal
# structured list building with a JSON Schema → output.structured
hsearch agent "Find 10 AI infra companies that raised a Series A in 6mo" --schema-file schema.json -f json

# NEW v0.8.0 — Tavily Extract as a third extractor, with relevance reranking
hsearch extract "https://en.wikipedia.org/wiki/Rocket_Lab" --provider tavily --query "Neutron rocket" --extract-depth advanced

# NEW v0.8.0 — Firecrawl scrape-control flags
hsearch search "openai" --provider firecrawl --firecrawl-store-in-cache --firecrawl-skip-tls --top 3

# NEW v0.7.0 — deep research agent (Tavily Research API): cited report in 10-60s
hsearch research "What rockets will AST SpaceMobile use in 2026?" --model mini

# NEW v0.7.0 — company entity search (Exa Company vertical)
hsearch search "AI defense tech startups" --mode company --top 5

# Exa category filter. Current enum (2026-08): company | publication | news |
# people | personal site | financial report. Retired names ('research paper',
# 'linkedin profile') are auto-normalized; pdf/github/tweet are deprecated
# upstream and now act as loose hints only.
hsearch search "Aurora Innovation quarterly results" --provider exa --category "financial report"

# Default search (uses Tavily if configured)
hsearch search "open source RAG frameworks 2026"

# Perplexity-style synthesized answer + cited sources
hsearch search "what's new in Claude Opus 4.7" --mode answer --days 7

# Multi-provider, parallel, deduped, JSON for piping into jq/agents
hsearch search "quantum chips Q1 2026" --all --top 8 --format json

# Maximize recall and source context for hard research questions
hsearch search "best sparse attention papers for long context" --mode recall --top 8 --format markdown

# Agent-friendly compact JSON preset (equivalent to --format json --top 5 unless overridden)
hsearch search "quantum chips Q1 2026" --agent --all

# Latest news from past 7 days
hsearch search "Anthropic announcements" --mode news --days 7

# Restrict to a domain, exclude another
hsearch search "vector DB benchmarks" --site arxiv.org --exclude reddit.com

# Academic / research with neural search + LLM summaries
hsearch search "speculative decoding" --mode academic --summary --top 5

# Force fresh Exa contents
hsearch search "OpenAI Devday 2026" --provider exa --max-age-hours 0

# Convert any URL into clean markdown (free via Jina Reader)
hsearch extract https://anthropic.com/news/claude-opus-4-7

# Full content for top-3 results, then pipe to your favorite LLM
hsearch search "kubernetes 1.32 changes" --extract-top 3 --format markdown

# JS-heavy top results? Use Firecrawl for the search→extract step
hsearch search "app router docs" --extract-top 3 --extract-provider firecrawl --format json
```

---

## Output formats

| Format     | When to use                                  |
| ---------- | -------------------------------------------- |
| `table`    | Default — pretty terminal output (Rich).     |
| `markdown` | Paste into docs/Slack/an LLM prompt.         |
| `json`     | Pipe into `jq`, agents, downstream scripts.  |
| `jsonl`    | Stream-friendly; one JSON object per line.   |
| `urls`     | Just URLs, one per line — for `xargs curl`.  |

`--agent` is a convenience preset for AI agents: it selects compact structured JSON and `--top 5` unless you explicitly pass `--format` or `--top`.

```bash
hsearch search "rust async runtimes" --format json | jq '.results[].url'
```

---

## Cache

The disk cache is enabled by default and now picks a TTL from the selected mode:

| Mode | Default TTL |
| ---- | ----------- |
| `realtime`, `news` | 300s |
| `finance`, `answer` | 900s |
| `general`, `fast`, `recall`, default | 3600s |
| `code`, `deep` | 14400s |
| `academic` | 86400s |

`--cache-ttl SECONDS` overrides the mode default for one call, and JSON output
includes `meta.cache_ttl_seconds` so agents can see the effective policy.

```bash
hsearch search "market open today" --mode finance --format json | jq '.meta.cache_ttl_seconds'
hsearch search "stable API reference" --mode academic --cache-ttl 604800
```

---

## Content extraction

`hsearch` doubles as a **URL-to-markdown** tool. Point it at any page and get
back clean, LLM-ready markdown — no scraping logic, no boilerplate stripping
on your side.

```bash
# Single URL (default: Jina Reader, free, fast)
hsearch extract https://anthropic.com/news/claude-opus-4-7

# Many URLs in parallel
hsearch extract https://a.com/x https://b.com/y https://c.com/z --concurrency 8

# JS-heavy / paywalled? Switch to Firecrawl
hsearch extract https://app.example.com/dashboard --provider firecrawl

# JSON output for downstream pipelines
hsearch extract https://arxiv.org/abs/2410.10630 --format json | jq '.results[].content'
```

### Flags

| Flag             | Type | Default    | Description                                          |
| ---------------- | ---- | ---------- | ---------------------------------------------------- |
| `--provider`,`-p`| str  | `jina`     | `jina` (free, fast) or `firecrawl` (JS render)       |
| `--format`, `-f` | str  | `markdown` | `markdown` or `json`                                 |
| `--concurrency`,`-c`| int | 4         | Parallel requests when extracting multiple URLs      |

`search --extract-top N` uses the same extractors. It defaults to Jina, but you can choose Firecrawl for JS-heavy pages with `--extract-provider firecrawl`.

### Two ways to get full page content

| Want…                                           | Use                                  |
| ----------------------------------------------- | ------------------------------------ |
| Markdown for **a known URL**                    | `hsearch extract <URL>`              |
| Markdown for **the top-N results of a search**  | `hsearch search "..." --extract-top 3` |

`--extract-top N` runs a search, then fetches and inlines the full markdown
for the first N deduped results into the `content` field — perfect for
pipelines that want both discovery and content in a single call.

```bash
# Search + auto-extract the top 3 hits, render as markdown
hsearch search "kubernetes 1.32 changes" --extract-top 3 --format markdown
```

> **Provider notes.** Jina Reader is free and handles ~95% of pages well. Use
> Firecrawl when a page is JS-heavy, requires interaction, or returns thin
> content from Jina.

---

## Documentation

- 📖 **[docs/USAGE.md](docs/USAGE.md)** — every flag, every mode, with examples
- 🔌 **[docs/PROVIDERS.md](docs/PROVIDERS.md)** — strengths/weaknesses of each provider + how to choose
- 🧩 **[docs/MCP.md](docs/MCP.md)** — use hsearch as a stdio MCP server
- 🤖 **[docs/SKILL.md](docs/SKILL.md)** — drop-in [Claude Code](https://docs.claude.com/en/docs/claude-code/skills) / [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill so your agent uses `hsearch` automatically

---

## Modes (`--mode`)

| Mode       | Default providers (fallback order)         | What it does                                               |
| ---------- | ------------------------------------------ | ---------------------------------------------------------- |
| `default`  | tavily                                     | General-purpose web search.                                |
| `general`  | tavily → brave                             | General web with broader fallback.                         |
| `news`     | brave → serper                             | Recent news; pair with `--days N`.                         |
| `academic` | exa                                        | Research papers, neural search.                            |
| `code`     | exa → brave                                | Code/library/SDK lookup.                                   |
| `realtime` | serper                                     | Past-day freshness for breaking events.                    |
| `shopping` | serper → brave                             | Product search.                                            |
| `video`    | serper → brave                             | Video results.                                             |
| `images`   | serper → brave                             | Image results.                                             |
| `places`   | serper → brave                             | Places / local search.                                     |
| `answer`   | tavily → brave                             | Synthesized **Answer panel** + sources. ⭐                 |
| `deep`     | exa                                        | Deep-reasoning / long-form research synthesis.             |
| `fast`     | exa → tavily                               | Low-latency search: Exa instant + Tavily ultra-fast.        |
| `recall`   | exa → tavily → brave → serper → firecrawl → jina | Broad, high-context search for maximum recall.       |

> If your preferred providers for a mode aren't configured, hsearch transparently falls back to whatever IS configured.

---

## Why use this over building it yourself?

Each provider's docs ship a Python snippet — copying 6 of them gives you 6
inconsistent error styles, 6 retry strategies, 6 result schemas, 6 places to
hide bugs. `hsearch` gives you **one schema** (`SearchResult`), **one retry
policy** (exponential backoff on 429/5xx, see `--retries`), **one cache**, and
**one CLI** that AI agents and shell scripts can both use.

---

## Development

```bash
pip install -e ".[dev]"
pytest                    # 113 tests, no real keys needed
```

All providers are mocked via `respx`. To add a new provider:

1. Drop a file in `hsearch/providers/<name>.py`, subclass `BaseProvider`.
2. Register in `hsearch/providers/__init__.py` and `hsearch/config.py`.
3. (Optional) add to `MODE_MAP` in `hsearch/router.py`.
4. Add a mocked test in `tests/`.

---

## License

[MIT](LICENSE) © 2026 [AnyGenIO](https://github.com/AnyGenIO)

---

## Acknowledgements

Built and battle-tested as part of the [AnyGen](https://github.com/AnyGenIO)
ecosystem of agent tooling. Inspired by the realization that an agent
juggling six API SDKs is not as good as an agent calling **one** unified CLI.
