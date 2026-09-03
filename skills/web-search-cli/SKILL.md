---
name: web-search-cli
description: "Use the native `web-search-cli` whenever an agent needs current web research, public code or documentation discovery, known-URL extraction, academic or video research, AI-grounded synthesis, durable research collection, result inspection, or CLI diagnostics. Invoke it with `uv run web-search-cli`; use `schema` and command `--help` to confirm the live surface before composing a call."
---

# web-search-cli

`web-search-cli` is the repository's native, JSON-first Typer CLI for the
web-search-mcp server. It covers web and code search, content extraction,
academic and video research, AI synthesis, durable collection jobs, analytics,
and local diagnostics.

In this repository, invoke it through `uv run`:

```powershell
uv run web-search-cli [GLOBAL-OPTIONS] COMMAND [ARGS] [COMMAND-OPTIONS]
```

The live CLI is the authority for syntax. Discover the tree before using an
unfamiliar path, then inspect the target command when an option matters:

```powershell
uv run web-search-cli --quiet schema
uv run web-search-cli --quiet search web --help
```

## Deterministic run loop

1. **Discover.** Run `schema` for the current command tree and `COMMAND --help`
   for exact required options. Completion criterion: the command path and every
   required input are known.
2. **Route.** Choose the narrowest command in the routing table below. Use
   `search web` for discovery, `content fetch` for known URLs, and `search
   fetch` for a cached GitHub repository snapshot. Completion criterion: one
   command is selected and its provider or local-data boundary is understood.
3. **Execute.** Put global options before the command and command options after
   it. Completion criterion: stdout is parsed as JSON or the documented raw
   output, and the process exit code is recorded.
4. **Inspect.** Read `data` first, then `meta`, `warnings`, status fields, and
   `suggested_next`. Completion criterion: the result is classified as complete,
   partial, empty, failed, or needing continuation.
5. **Continue or corroborate.** Follow a returned cursor/offset before calling
   the result complete. For factual research, read the strongest source pages
   and cross-check material claims with independent authoritative evidence.
   Completion criterion: every continuation is handled or explicitly deferred,
   and unsupported or single-source claims are labeled.

A retrieval run is complete only when the requested evidence is present, all
continuation signals were handled or intentionally deferred, and partial or
single-source results are labeled as such.

## Global options

Global options belong before the command path. The canonical long forms are:

| Option | Default | Use |
| --- | --- | --- |
| `--quiet` / `-q` | `false` | Remove the inline `rules`, `skills`, and `feedback` fields from JSON responses. |
| `--profile TEXT` | `full` | Set the active profile in runtime metadata and profile-aware integrations. Use `reference tools --profile ...` to inspect MCP coverage; it does not hide direct CLI commands. |
| `--log-level TEXT` | `error` | Set stderr log verbosity. |
| `--log-format TEXT` | `text` | Use `json` for one JSON object per stderr log line. |
| `--debug` | `false` | Set debug logging while keeping command data on stdout. |
| `--non-interactive` | `true` | Keep prompts disabled; this is the agent-safe default. |
| `--raw` | `false` | Emit the selected data without the normal JSON envelope. |
| `--fields a,b` | unset | Project selected top-level keys from `data`; projection is shallow. |
| `--yes` / `-y` | `false` | Bypass a confirmation when a command supports one. Treat it as a reserved mutation flag. |
| `--dry-run` | `false` | Preview `feedback create`, `feedback close`, and `feedback transition` without changing files. |
| `--install-completion` | — | Install Typer shell completion. |
| `--show-completion` | — | Print Typer shell completion. |

`--quiet`, `--raw`, and `--fields` are especially useful for scripts:

```powershell
uv run web-search-cli --quiet --fields results,run_key search web `
  --query "Python 3.13 asyncio changes" `
  --research-goal "identify versioned asyncio changes from authoritative sources"

uv run web-search-cli --raw schema | ConvertFrom-Json
uv run web-search-cli --debug --log-format json search web `
  --query "exact error text" --research-goal "diagnose this error" --no-rewrite
```

The `-q` short option is both the global quiet flag and the local `search web`
query alias. Keep global `-q` before the command; use `--query` when ambiguity
would make a script harder to read.

Special flags are plain-text exceptions to the normal envelope:

- `uv run web-search-cli --version` (or `-V`) prints the package version.
- `uv run web-search-cli --brief` prints the one-paragraph CLI identity.
- `COMMAND --help` emits a structured JSON help envelope.
- `getskill` and `skills NAME` print markdown verbatim.
- `server start` is a long-running process launcher, not a one-shot data call.

## JSON contract

Normal one-shot success output is one JSON object on stdout:

```json
{
  "schema_version": "1.0",
  "data": {"command-specific": "payload"},
  "meta": {
    "command": "search web",
    "profile": "full",
    "quiet": true,
    "log_level": "error",
    "log_format": "text",
    "debug": false,
    "non_interactive": true,
    "raw": false,
    "fields": null,
    "yes": false,
    "dry_run": false,
    "duration_ms": 123.4,
    "generated_at": "2026-09-02T16:00:00Z"
  },
  "suggested_next": []
}
```

`meta` may also contain `run_key` when the command produced one. Unless
`--quiet` is set, the top-level response also contains the inline `rules`,
`skills`, and `feedback` fields. These fields can be large; do not confuse them
with command data.

`--fields` selects only top-level keys in `data`; it does not select nested
paths. In `--raw` mode, a list is written one item per line, a one-key mapping
is reduced to its value, and other values are serialized as JSON. Use the
normal envelope when structure matters.

Normal command errors are one JSON object on stderr and a non-zero exit code:

```json
{
  "error": {
    "kind": "usage_error",
    "code": "usage_error",
    "message": "...",
    "hint": "...",
    "suggestion": "...",
    "exit_code": 2,
    "context": {"command": "search web"}
  }
}
```

With `--quiet`, the error also omits inline rules, skills, and feedback. Keep
stdout and stderr separate when parsing. A command may emit a useful payload
and then exit non-zero for a timeout or strict validation path; inspect both
before retrying.

| Exit code | Meaning |
| ---: | --- |
| `0` | Success |
| `1` | Internal error |
| `2` | Usage error |
| `6` | Rate limited |
| `7` | Validation error |
| `8` | Network error |
| `9` | Schema error |
| `10` | Authentication error |
| `11` | Permission error |
| `12` | Provider error |
| `13` | Timeout |
| `20` | Resource not found |
| `30` | State conflict |

## Live command tree

`schema` returns both a nested `command_tree` and a flat `commands` list. The
current leaf command paths are:

```text
schema
 doctor
 getskill [--dev]
 recommend TASK...
 feedback create|list|show|close|transition
 skills [NAME]
 jobs list|get|wait|cancel|resume
 results search
 reference tools|external-tools
 research deep|collect
 search quick|web|inspect|postmortem|code|fetch|academic
 content fetch
 links discover|similar
 inference describe|validate|chain
 ai gemini|grok
 youtube search|transcript|channel
 analytics query|report
 experiments list|enable|disable|conclude|stats|create
 server [start]
 sitemap generate
```

Use `uv run web-search-cli --quiet schema` rather than relying on this list if
another checkout may have a different version.

## Route selection

| Need | Command | MCP counterpart or role |
| --- | --- | --- |
| Fast terminology reconnaissance | `search quick` | `quick_web_search` |
| Multi-provider URL discovery | `search web` | `web_search` |
| Public code or implementation examples | `search code` | `code_search` |
| Read a current GitHub snapshot | `search fetch` | `code_fetch` |
| Scholarly papers and citation graph | `search academic` | `academic_search` |
| Read one or many known URLs | `content fetch` | `fetch` |
| Discover outbound links | `links discover` | `discover_links` |
| Expand from a known good URL | `links similar` | `composio_similarlinks` |
| Google-grounded answer | `ai gemini` | `gemini_search` |
| xAI live web/social synthesis | `ai grok` | `grok_search` |
| Find videos | `youtube search` | `youtube_search` |
| Extract video or channel speech | `youtube transcript` / `youtube channel` | `youtube_transcript` |
| Autonomous research report | `research deep` | CLI deep-research backend at `DEEP_RESEARCH_URL` |
| Deterministic evidence bundle | `research collect` | CLI collection workflow |
| Local analytics question | `analytics query` | CLI-only |
| Fixed analytics report | `analytics report` | CLI-only |
| Persisted result lookup | `results search` | CLI-only |
| Search-run diagnosis | `search inspect` / `search postmortem` | CLI-only |

`reference tools` reports the MCP mapping only; it is not a complete listing of
CLI-only commands such as `jobs`, `results`, `research`, and experiments.

## Search commands

### `search web`

Run the full multi-provider pipeline. `--research-goal` is required and
`--query` can be repeated; at most four non-blank seed queries are retained.
Query rewriting is enabled by default.

```powershell
uv run web-search-cli search web `
  --query "FastMCP middleware retry behavior" `
  --research-goal "find current official middleware retry guidance"

uv run web-search-cli search web `
  --query "exact exception text" `
  --research-goal "locate the source of this exception" `
  --no-rewrite --after-date 2025-01-01 --domain-boost github.com `
  --diagnostics
```

| Option | Default or values | Use |
| --- | --- | --- |
| `--query` / `-q TEXT` | repeatable, required | Search seed; repeat up to four times. |
| `--rewrite` / `--no-rewrite` | `rewrite` | Use `--no-rewrite` for exact errors, identifiers, URLs, versions, hashes, and quoted strings. |
| `--research-goal TEXT` | required | State the decision or evidence needed, not just the topic. |
| `--reranking-instructions TEXT` | unset | Tell rerankers which source types to prioritize or demote. |
| `--date-range day\|week\|month\|year` | unset | Relative freshness window. |
| `--after-date YYYY-MM-DD` | unset | Inclusive lower publication bound. |
| `--before-date YYYY-MM-DD` | unset | Inclusive upper publication bound. |
| `--language CODE` | unset | ISO 639-1 or BCP-47 language. |
| `--region CODE` | unset | ISO 3166-1 alpha-2 region bias/filter. |
| `--include-undated` / `--exclude-undated` | unset | Choose how undated results behave under absolute date windows. |
| `--domain-boost DOMAIN` | repeatable | Move matching domains toward the front. This is the current domain control exposed by the CLI. |
| `--diagnostics` | `false` | Add detailed pipeline diagnostics under `data._diagnostics`. |

The parser also accepts `--searxng-category`, `--searxng-engine`,
`--searxng-language`, `--searxng-pageno`, `--searxng-time-range`, and
`--searxng-safesearch`. They are currently collected by the CLI adapter as
obsolete options and are not propagated to the search pipeline. Use the
provider-neutral date, locale, and domain options above for effective
constraints. The CLI does not expose the older `--site-filter`,
`--domain-filter`, or `--domain-block` options.

The result payload normally contains `results`, `total_results`,
`providers_used`, optional `warnings`, and a `run_key`. Each result can carry
`provider_count`, `providers`, `score`, and `published_date`. Treat
`provider_count >= 2` as stronger agreement, not as proof; inspect the source
content before relying on a claim.

### `search quick`

Run the fast Parallel AI reconnaissance path. In practice, provide at least
one query and one objective; the command validates both at runtime even though
the generated schema represents these nullable options as optional.

```powershell
uv run web-search-cli search quick `
  --search-query "latest Python releases" `
  --objective "map recent release announcements"
```

`--query` aliases `--search-query`, and `--objective` aliases
`--research-goal`. The query options are repeatable; provide one objective
through either alias. Additional options are `--max-results`, `--max-chars-total`,
`--max-chars-per-result`, `--client-model`, `--session-id`,
`--include-domain` (repeatable), `--exclude-domain` (repeatable),
`--after-date`, `--location`, `--max-age-seconds`, `--timeout-seconds`, and
`--disable-cache-fallback`.

Use short 3–6 word queries for reconnaissance. If the result needs exact
provider agreement or a broad URL set, follow with `search web`.

### `search code`

Search public code, documentation, repository implementations, or Hugging Face
assets. The query is required; all other options are optional.

```powershell
uv run web-search-cli search code `
  --query "FastMCP tool registration" `
  --repository prefecthq/fastmcp --language Python

uv run web-search-cli search code `
  --query "text generation models" --mode huggingface `
  --huggingface-task text-generation --huggingface-min-downloads 1000
```

Core options:

- `--query TEXT`
- `--research-goal TEXT`
- `--repository` / `--repositories OWNER/NAME` (repeatable, max 25)
- `--language`, `--path`, `--filename`, `--extension`
- `--regexp` / `--no-regexp`
- `--deep` / `--no-deep`
- `--repo-name`, `--library-name`, `--topic`
- `--mode code|docs|discovery|huggingface` (default `code`)

Hugging Face mode adds:
`--huggingface-type models|datasets|both` (default `both`),
`--huggingface-sort-by similarity|likes|downloads|trending|updated` (default
`similarity`), `--huggingface-hybrid/--no-huggingface-hybrid`,
`--huggingface-min-likes` (default `0`), `--huggingface-min-downloads` (default
`0`), `--huggingface-task`, `--huggingface-license`,
`--huggingface-language`, `--huggingface-modified-after`,
`--huggingface-min-param-count` (default `0`), and
`--huggingface-max-param-count`.

### `search fetch`

Explore a cached GitHub repository snapshot. `--repository OWNER/NAME` is
required. Use `--query` for repository-wide matching, `--path` for a file or
directory, and `--symbol` for definitions with callers/callees. A query returns
match lines; follow a hit with `--path` and optional line bounds to read the
full file.

```powershell
uv run web-search-cli search fetch `
  --repository prefecthq/fastmcp --query "class FastMCP" --language Python

uv run web-search-cli search fetch `
  --repository prefecthq/fastmcp --path src/fastmcp/server/server.py `
  --start-line 1 --end-line 120

uv run web-search-cli search fetch `
  --repository prefecthq/fastmcp --symbol FastMCP
```

Options are `--repository`, `--query`, `--path`, `--symbol`, `--ref`,
`--regexp/--no-regexp`, `--max-matches` (default `25`), `--context-lines`
(default `3`), `--start-line`, `--end-line`, `--depth`, `--language`,
`--filename`, `--path-glob`, `--exclude-glob`,
`--case-sensitive/--no-case-sensitive`, and `--cursor`.

The first call for a repository materializes and indexes a snapshot; later
calls within its TTL are faster. Successful responses include
`resolved_commit` and `cache_age_seconds`. Search pagination uses
`has_more` and `next_cursor`.

### `search academic`

Search deduplicated scholarly results. The CLI requires `--query`; use
citation-graph options when a paper or author is already known.

```powershell
uv run web-search-cli search academic `
  --query "retrieval augmented generation evaluation" `
  --year-from 2024 --field-of-study "Computer Science" `
  --open-access-only --sort citations
```

Options:

- `--query TEXT` (required)
- `--limit INT` (default `5`, clamped to `1..20`)
- `--source SOURCE` (repeatable)
- `--source-type general|polish|archive`
- `--year-from INT`, `--year-to INT`
- `--field-of-study FIELD` (repeatable)
- `--venue TEXT`
- `--open-access-only/--no-open-access-only`
- `--sort relevance|citations|date` (default `relevance`)
- `--cited-by ID`, `--references ID`, `--author-id ID`

## Content and link commands

### `content fetch`

One command handles one URL, repeated URLs, or an input file. It replaces older
`content get` and `content batch` paths.

```powershell
# One URL
uv run web-search-cli content fetch --url "https://example.com/article"

# Several URLs
uv run web-search-cli content fetch `
  --url "https://example.com/a" --url "https://example.com/b"

# URL lines or JSONL records; use - for stdin
uv run web-search-cli content fetch --input-file urls.txt
uv run web-search-cli content fetch --input-file -
```

An input file may contain one URL per line or one JSON object per line with a
`url` or `input_url` field. Blank lines are ignored. Use `--offset` for a
single-result content window and `--cursor` to continue a bulk fetch.

| Option | Default | Use |
| --- | --- | --- |
| `--url URL` | unset | Repeat for multiple URLs. |
| `--input-file PATH` | unset | Read URL lines or JSONL; `-` reads stdin. |
| `--offset INT` | `0` | Start offset for the selected content window. |
| `--cursor TEXT` | unset | Continue a bulk response. |
| `--ai-summary/--no-ai-summary` | `false` | Add a source-grounded Gemini summary. |
| `--focus-query TEXT` | unset | Focus the summary. |
| `--include-metadata/--no-include-metadata` | `true` | Include page metadata. |
| `--include-links/--no-include-links` | `false` | Include extracted links. |
| `--max-links INT` | `25` | Cap links when included. |
| `--strip-selectors TEXT` | unset | Comma-separated CSS selectors to remove before extraction. |
| `--output PATH` | unset | For a single result, write markdown content; for bulk, write the JSON payload. |

The response has `data.mode` (`single` or `bulk`). For a single response, inspect
`data.results[0].window.has_more` and `window.next_offset`; continue with the
same URL and `--offset <next_offset>`. For bulk, inspect `data.has_more` and
`data.cursor`; continue with `--cursor <cursor>`. `suggested_next` often contains
the exact continuation command.

### `links discover`

Discover links from a page or sitemap:

```powershell
uv run web-search-cli links discover `
  --url "https://docs.python.org/3/" --same-domain-only --max-links 200
```

Options: `--url` (required), `--max-links` (default `100`),
`--include-external/--no-include-external` (default `true`),
`--same-domain-only/--no-same-domain-only` (default `false`), and
`--strip-selectors`. If `data.has_more` is true, rerun with a larger
`--max-links`; this command has no cursor option.

### `links similar`

Find neural-similar pages from a known-good URL:

```powershell
uv run web-search-cli links similar `
  --url "https://docs.python.org/3/library/asyncio-task.html" `
  --num-results 8 --include-domain docs.python.org
```

Options: `--url` (required), `--num-results` (default `5`),
`--search-type` (default `neural`), `--category`, and repeatable
`--include-domain` / `--exclude-domain`.

## AI, video, and research commands

### `ai gemini` and `ai grok`

Use Gemini for a Google-grounded answer with citations. Use Grok for native xAI
live search and synthesis, including social results when available.

```powershell
uv run web-search-cli ai gemini `
  --query "What changed in Python 3.13 asyncio?" `
  --research-goal "summarize versioned changes with citations"

uv run web-search-cli ai grok `
  --query "current status of the EU AI Act" `
  --research-goal "compare current official and social reporting" `
  --num-results 8
```

`ai gemini` options: required `--query`,
`--structured-output/--no-structured-output` (default `false`), and
`--research-goal`.

`ai grok` options: required `--query`, optional `--research-goal` (default
empty), `--model` (provider chooses the default), `--num-results` (default
`5`), repeatable `--allowed-domain` and `--excluded-domain`, and `--timeout`.
Provide a research goal even though the CLI does not require it.

### YouTube

First discover videos, then transcribe selected videos:

```powershell
uv run web-search-cli youtube search --query "Rust Tokio tutorial" --num-results 5
uv run web-search-cli youtube transcript `
  --video-id-or-url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" `
  --format timestamped --language en
```

`youtube search` takes required `--query` and `--num-results` (default `5`).

`youtube transcript` takes required `--video-id-or-url`, optional `--language`,
`--translate-to`, `--format text|timestamped|json|markdown` (default `text`),
`--backend auto|ytdlp|api`, `--include-summary`, and `--summary-focus`.

For recent uploads from a channel, use `youtube channel`:

```powershell
uv run web-search-cli youtube channel `
  --channel "@python" --max-videos 10 --format markdown `
  --page-token "<next-page-token>" --output channel.json
```

`youtube channel` options are required `--channel`, `--max-videos` (default
`20`), `--language`, `--translate-to`, `--format text|timestamped|json|markdown`
(default `markdown`), `--backend`, `--include-summary`, `--summary-focus`,
`--page-token`, and `--output`. Continue with `data.next_page_token` when
present. Channel results report per-video partial failures.

### `research deep`

Run the autonomous deep-research backend. The canonical depth values are
`quick`, `standard` (default), and `deep`.

```powershell
uv run web-search-cli research deep `
  --query "How does hybrid RAG retrieval affect recall?" `
  --depth deep --output report.md
```

Options: required `--query`; `--depth`; `--with-images/--no-with-images`
(default `false`); `--language-code`; `--token-budget`; `--team-size`;
`--endpoint`; and `--output`. `--output` writes the returned
`report_markdown` to a file and adds `report_path` to the response. The backend
requires the configured `DEEP_RESEARCH_URL` endpoint.

### `research collect`

Create a deterministic evidence bundle from search results and fetched source
pages. The collection report is not an AI synthesis; `--ai-summary` optionally
adds source-grounded summaries to fetched items.

```powershell
uv run web-search-cli research collect `
  --query "Python 3.13 asyncio changes" `
  --research-goal "collect official release and documentation evidence" `
  --output-dir .\research\python-313 `
  --top-results 5
```

Options: required `--query`, `--research-goal`, and `--output-dir`; optional
`--top-results` (default `5`), `--rewrite/--no-rewrite` (default `true`),
`--ai-summary/--no-ai-summary`, `--no-wait`, and `--idempotency-key`.

The waiting path writes `search.json`, `sources.json`, `report.md`,
`manifest.json`, and `sources/source-*.md` below the output directory. With
`--no-wait`, the command submits a durable local job; use the returned job ID
with `jobs get` or `jobs wait`.

## Operational commands

### `jobs`

Durable local jobs are used by non-blocking research collection:

```powershell
uv run web-search-cli jobs list --limit 20
uv run web-search-cli jobs get JOB_ID
uv run web-search-cli jobs wait JOB_ID --timeout-seconds 300
uv run web-search-cli jobs cancel JOB_ID
uv run web-search-cli jobs resume JOB_ID
```

`jobs wait` also accepts `--poll-interval-seconds` (default `2`). It returns a
payload with `timed_out`; a timeout exits with code `13`. The default job store
is `duckdb_data/cli/jobs.sqlite`, overridable with `WEB_SEARCH_CLI_JOBS_DB`.

### `results search`

Search retained MCP, CLI, and deep-research result payloads:

```powershell
uv run web-search-cli results search `
  --query "asyncio" --kind cli --source "search web" --limit 20
```

Options are `--query` / `-s`, `--kind mcp|cli|deep_research`, `--source`,
`--limit` (default `50`, range `1..200`), and `--db-path`. The default store
is the SQLite database selected by `WEB_SEARCH_CLI_JOBS_DB`.

### Search-run inspection

`search web` returns a `run_key`. Use it to inspect a run or summarize provider
and reranker failures from the read-only analytics database:

```powershell
uv run web-search-cli search inspect --run-key RUN_KEY
uv run web-search-cli search postmortem --run-key RUN_KEY
```

Both commands accept `--db-path`.

### `analytics query` and `analytics report`

Use the CLI analytics surface for local or MotherDuck questions and fixed
reports:

```powershell
uv run web-search-cli analytics query `
  --question "top providers by error count in the last 7 days" --max-rows 25
uv run web-search-cli analytics report `
  --report-name "provider-performance" --days 14
```

`analytics query` takes required `--question`, `--scope local|motherduck`
(default `local`), `--max-rows` (default `100`), and `--db-path`.

`analytics report` takes required `--report-name`, `--days` (default `7`), and
`--db-path`. An invalid report name returns the available report names in its
error hint. Analytics database paths are read by the command; use the
repository's read-only database convention for external inspection.

### `experiments`

A/B experiment configuration is loaded from `AB_CONFIG_PATH` or the default
`duckdb_data/experiments/experiments.yaml`.

```powershell
uv run web-search-cli experiments list
uv run web-search-cli experiments stats EXPERIMENT_ID
uv run web-search-cli experiments enable EXPERIMENT_ID
uv run web-search-cli experiments disable EXPERIMENT_ID
uv run web-search-cli experiments conclude EXPERIMENT_ID --winner VARIANT_KEY
```

`experiments create` is non-interactive by default and therefore expects a JSON
string in `--config`:

```powershell
uv run web-search-cli experiments create `
  --config '{"experiment_id":"rerank-test","layer":"reranking","status":"draft","hypothesis":"Bi-encoder first improves latency","primary_metric":"p95_latency_ms","traffic_pct":10,"variants":[{"variant_key":"control","weight":1,"description":"current pipeline"},{"variant_key":"bi-first","weight":1,"description":"bi-encoder before cross-encoder"}]}'
```

The mutation commands write the YAML configuration. Valid statuses are
`draft`, `running`, `paused`, and `concluded`; `conclude` requires an existing
variant key. The global `--dry-run` flag currently previews feedback mutations,
not experiment mutations.

### `inference`

Inspect model/provider/chain registration without making provider calls:

```powershell
uv run web-search-cli inference describe
uv run web-search-cli inference validate --strict
uv run web-search-cli inference chain fast_rewrite
```

`validate --strict` exits non-zero when validation fails. `chain NAME` reports
registered provider details and environment-variable names, not secret values.

### `reference`, `schema`, `doctor`, `skills`, and `getskill`

```powershell
uv run web-search-cli reference tools --profile research
uv run web-search-cli reference external-tools
uv run web-search-cli doctor
uv run web-search-cli skills
uv run web-search-cli skills web-search-cli
uv run web-search-cli getskill
uv run web-search-cli getskill --dev
uv run web-search-cli recommend "Find current official docs for FastMCP middleware"
```

- `reference tools` emits the MCP-tool-to-command mapping for a profile.
- `reference external-tools` lists companion paths such as DuckDB, WSL Grafana,
  and Phoenix.
- `schema` emits the generated command tree; `doctor` checks local package,
  skill, repository, DuckDB/SQLite, and analytics-schema readiness without
  provider calls. It does not prove remote credentials are valid.
- `skills` with no name returns a JSON catalog; `skills NAME` prints that
  skill's markdown verbatim. `getskill` prints the bundled user skill, or the
  developer skill with `--dev`.
- `recommend TASK...` returns route/decomposition metadata without executing a
  command or calling a provider.

### `feedback`

When a call produces an error, ambiguous output, a missing field, or confusing
truncation, record the observation before explaining it:

```powershell
uv run web-search-cli feedback create `
  --message "search web returned has_more without a usable continuation" `
  --type bad-output --command "search web" --exit-code 0
uv run web-search-cli feedback list --status open
uv run web-search-cli feedback show 001
```

`feedback create` options: required `--message` / `-m`; `--type` / `-t`
(`bug`, `requirement`, `suggestion`, `bad-output`, default `bug`); `--command`;
and `--exit-code` (default `0`).

`feedback list` accepts `--type` / `-t` and `--status` / `-s`. Valid statuses
are `open`, `in-progress`, `resolved`, and `closed`. `feedback show ID` reads an
entry, `feedback close ID` closes it, and `feedback transition ID --status
STATUS` sets a valid status. Put global `--dry-run` before the command to preview
create, close, or transition without writing `feedback/{id}.json`.

## Sitemap and server

### `sitemap generate`

Generate a Tavily Map sitemap:

```powershell
uv run web-search-cli sitemap generate `
  --url "https://docs.python.org/3/" --max-depth 2 `
  --select-paths "/3/library/"
```

Options: required `--url`; `--instructions`; `--max-depth` (default `1`),
`--max-breadth` (default `20`), `--limit` (default `50`), repeatable
`--select-paths`, `--select-domains`, `--exclude-paths`, `--exclude-domains`,
and `--allow-external/--no-allow-external` (default `false`).

### `server start`

Launch the MCP server. Use one transport flag; if multiple are supplied, the
selection order is HTTP, then SSE, then stdio. Stdio is the default.

```powershell
uv run web-search-cli server start --stdio
uv run web-search-cli server start --http --host 127.0.0.1 --port 8000
uv run web-search-cli server start --sse --host 127.0.0.1 --port 8000
```

Options are `--http/--no-http`, `--sse/--no-sse`, `--stdio/--no-stdio`,
`--host`, and `--port`. `uv run web-search-cli server` invokes the same
callback; `server start` is clearer in scripts.

## Research quality and continuation

Use this compact evidence loop:

1. Start broad with `search quick` when terminology or the landscape is
   unknown.
2. Use `search web` with a specific `--research-goal`; leave rewriting on for
   concepts and use `--no-rewrite` for exact literals.
3. Fetch selected sources with `content fetch`, not the search command. Prefer
   official documentation for API contracts, GitHub issues/PRs for bugs and
   migrations, papers for scholarly claims, and community sources for
   practitioner experience.
4. Inspect `provider_count`, dates, source type, `status`, `warnings`, and
   window/cursor fields. Treat a single provider or short/blocked page as
   provisional.
5. Use `ai gemini` or `ai grok` only after the question and source set are
   clear. Use `research deep` for an autonomous report and `research collect`
   when reproducible local artifacts are needed.

For every fetched result, check `window.has_more`, `window.next_offset`,
`data.has_more`, `data.cursor`, `data.next_cursor`, or
`data.next_page_token` when present. Continue with the matching command and
opaque token rather than inventing a new offset or query. `suggested_next` is a
hint generated from the payload; validate it against `schema` when composing
scripts.

## Environment and readiness

The CLI loads `.env` from the repository root and then the current working
directory. Never place real credentials in commands or this skill. Common
settings include:

| Capability | Environment |
| --- | --- |
| Search providers | `SEARXNG_BASE_URL`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, `JINA_API_KEY`, `LANGSEARCH_API_KEY`, `SEARCH_ROUTER_API_KEY` |
| Query rewriting | `CEREBRAS_API_KEY`, `GROQ_API_KEY`, `HF_TOKEN` |
| Parallel reconnaissance | `PARALLEL_API_KEY` |
| Gemini/Grok | `GEMINI_API_KEY`, `XAI_API_KEY` |
| Public GitHub/code hydration | `GITHUB_TOKEN` or `GH_TOKEN` |
| Similar links | `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID` |
| Academic rate/full-text access | `S2_API_KEY`, `OPENALEX_API_KEY`, `PUBMED_API_KEY`, `CORE_API_KEY` |
| Deep research | `DEEP_RESEARCH_URL` |
| YouTube transcript fallback | `YOUTUBE_TRANSCRIPT_PROXY_URL` |
| Local job/result store | `WEB_SEARCH_CLI_JOBS_DB` |
| A/B experiment YAML | `AB_CONFIG_PATH` |

Use `doctor` for local readiness and read the command's structured checks. A
passing doctor result does not authenticate a remote provider; provider errors
remain actionable command results.

## Maintenance source of truth

When this skill needs updating, compare it with:

- `src/kindly_web_search_mcp_server/cli/app.py` — global flags and registration
- `src/kindly_web_search_mcp_server/cli/commands/` — command paths/options
- `src/kindly_web_search_mcp_server/cli/introspection.py` — generated schema
- `src/kindly_web_search_mcp_server/cli/output.py` — envelopes, projection, continuation
- `src/kindly_web_search_mcp_server/cli/errors.py` and `exit_codes.py` — failures
- `src/kindly_web_search_mcp_server/cli/reference_data.py` — MCP coverage matrix

The minimum freshness check after a command change is:

```powershell
uv run web-search-cli --quiet schema
uv run web-search-cli --quiet COMMAND --help
```
