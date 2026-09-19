---
name: web-search-cli
description: User skill for web-search-cli — JSON-first Typer CLI driving the web-search-mcp tool surface.
---

# web-search-cli

Native JSON-first Typer CLI for the `web-search-mcp` server. Run every command with `uv run web-search-cli …`. Standard output on stdout is a JSON envelope — `{schema_version, data, meta, suggested_next}` — with inline `rules`, `skills`, and `feedback` context unless you pass `--quiet`.

## When to use

Use this skill whenever you need to drive web search, fetch, crawl, YouTube transcripts, AI synthesis, link discovery, sitemap generation, or research / analytics from a shell, script, or orchestrated pipeline. Skip it for in-conversation MCP tool calls — the MCP client is already wired there.

## Authentication

Set provider keys as environment variables before invoking the CLI. Missing keys produce an `auth_error` `CliError` envelope with the right variable name in the hint.

- `BRAVE_API_KEY` — Brave web search.
- `TAVILY_API_KEY` — Tavily web search and sitemap map.
- `GEMINI_API_KEY` — Gemini search, AI summaries, RankLLM.
- `XAI_API_KEY` — xAI Grok native web / X search (`ai grok`).
- `PARALLEL_API_KEY` — Parallel AI quick web search (`search quick`).
- `GOOGLE_API_KEY` — YouTube Data API v3 (`youtube transcript`, `youtube channel`, `search quick --mode youtube`).

Optional: `JINA_API_KEY`, `SEARXNG_BASE_URL`, `DEGOOG_BASE_URL`, `GROK_BACKEND=xai`, `NANOGPT_API_KEY`, `GITHUB_TOKEN`.

## Output contract

Successful command:

```
{
  "schema_version": "1.0",
  "data": <command payload>,
  "meta": {"command": "...", "profile": "full", "duration_ms": ..., "generated_at": "...Z", ...},
  "suggested_next": ["uv run web-search-cli …", …],
  "rules": [...],
  "skills": [...],
  "feedback": "..."
}
```

Errors go to stderr as JSON with an `error` block carrying `kind`, `code`, `message`, `hint`, `suggestion`, `exit_code`, and `context`. The hint is the actionable next step; read it before retrying.

Useful global flags:

- `--quiet` / `-q` — drop `rules`/`skills`/`feedback` from the envelope.
- `--raw` — bare value lines for piping.
- `--fields key1,key2` — project the top-level keys.
- `--profile` — tool visibility profile (`default`, `research`, `media`, `diagnostic`, `experimental`, `full`).
- `--log-format json` — JSONL logs on stderr.
- `--human` — indented JSON with data plus command metadata; `--agent` forces
  the standard structured envelope.
- `--dry-run` — preview `feedback create|close|transition` writes.
- `--brief`, `--version`, `--help` — plain-text short forms.

## Discovery sequence

When you don't know the exact command or flags, walk this ladder:

```
uv run web-search-cli --brief
uv run web-search-cli schema
uv run web-search-cli <group> --help
uv run web-search-cli <group> <subcommand> --help
uv run web-search-cli reference tools
uv run web-search-cli reference external-tools
uv run web-search-cli doctor
```

## Workflows

Quick web search → focused fetch → collected brief:

```
uv run web-search-cli search quick \
  --search-query "vector database hybrid retrieval" \
  --search-query "graph rag citation grounding" \
  --objective "compare vector hybrid retrieval vs graph RAG for citation grounding" \
  --num-results 10

uv run web-search-cli content fetch --url <result.url> --focus-query "hybrid retrieval ranking"

uv run web-search-cli research collect \
  --query "hybrid retrieval augmented generation" \
  --research-goal "produce a comparative brief"
```

YouTube discovery + transcript + summary:

```
# Discovery (YouTube search terms go through --query, NOT --search-query)
uv run web-search-cli search quick --mode youtube --query "agent design patterns"

# Transcript + optional Gemini summary
uv run web-search-cli youtube transcript \
  --video-id-or-url "https://www.youtube.com/watch?v=…" \
  --include-summary --summary-focus "agent design patterns"

# Whole-channel transcription (uploads)
uv run web-search-cli youtube channel --channel "@SomeChannel" --max-videos 5 --include-summary
```

Bounded site crawl into the index:

```
uv run web-search-cli content crawl \
  --url https://example.com/docs \
  --max-depth 2 --max-pages 25

Analytics over the local DuckDB:

```
uv run web-search-cli analytics query --question "search p95 latency last 24h"
uv run web-search-cli analytics report --report-name daily_search_health
```

Feedback loop after a bad call:

```
uv run web-search-cli feedback create \
  --message "search quick returned empty for \`…\` even though keys are set" \
  --type bug --command "uv run web-search-cli search quick …" --exit-code 0
```

## Gotchas

- **Crawl is `content crawl`, not `crawl`.** The MCP tool is `crawl_web`; the CLI exposes it as `uv run web-search-cli content crawl --url …`.
- **YouTube discovery uses `search quick --mode youtube`, not `youtube search`.** `youtube` only has `transcript` and `channel`; `youtube search` is not a command. Pass the search terms via `--query` (single string), not `--search-query`.
- **YouTube channel discovery is via `youtube channel` for transcription; for *finding* channels or videos, use `search quick --mode youtube`.**
- **Every search tool needs `research_goal` / `--objective`.** Even the quick path rejects calls without an objective; pass `--objective "..."` (alias: `--research-goal`).
- **`content fetch` summary is opt-in.** Use `--ai-summary` (default off); the legacy `--summary-mode` flag is gone.
- **Provider errors are real, not noise.** A `provider_error` / `auth_error` envelope means the env var or upstream is wrong — fix it and retry, do not retry blindly.
- **Auto-persistence is on by default.** Every non-help call writes a row to `duckdb_data/cli/`; query it with `uv run web-search-cli results search --query "…"`.
