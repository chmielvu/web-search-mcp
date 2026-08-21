---
name: web-search-cli
description: JSON-first native Typer CLI for the web-search-mcp server. Use when an agent needs to discover, fetch, or analyse web content from a shell or scripted context, or to validate CLI readiness, inspect the command schema, or look up MCP-tool to CLI-command coverage.
---

# web-search-cli

The `web-search-cli` is the native, JSON-first command-line surface for the
Kindly Web Search MCP server. It covers the supported subset of MCP tools
exposed by `server.py` and adds operational commands (`schema`, `doctor`,
`getskill`, `reference`, `server`, `experiments`) that are only meaningful
outside of MCP.

All commands emit a single JSON envelope on stdout (or a JSON error envelope
on stderr) and use the exit codes defined in
`src/kindly_web_search_mcp_server/cli/exit_codes.py`.

## Invocation

```powershell
web-search-cli [GLOBAL-OPTIONS] COMMAND [ARGS] [COMMAND-OPTIONS]
```

### Global options (defined on `@app.callback()`)

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--brief` | bool | `False` | Emit one-paragraph tool identity and exit. |
| `--quiet` / `-q` | bool | `False` | Suppress rules, skills, and feedback from JSON response payload. |
| `--raw` | bool | `False` | Emit bare value lines (one per line) for clean pipe processing. |
| `--fields` | string | — | Comma-separated field projection to reduce response payload size. |
| `--yes` / `-y` | bool | `False` | Skip confirmation prompt (required in non-interactive mode). |
| `--dry-run` | bool | `False` | Preview feedback create, close, and transition without modifying files. |
| `--profile` | string | `full` | Active capability profile: `default`, `research`, `media`, `diagnostic`, `experimental`, `full`. |
| `--log-format` | string | `text` | Stderr log format (`text` or `json` for JSONL stream). |
| `--log-level` | string | `error` | Log level for stderr diagnostics. |
| `--debug` | bool | `False` | Set log level to `DEBUG` and emit application logs on stderr. Structured command output remains on stdout. |
| `--non-interactive` | bool | `True` | Disallow interactive prompts. |

All global flags also accept the corresponding `WEB_SEARCH_CLI_*` envvar
(e.g. `WEB_SEARCH_CLI_DEBUG=true`, `WEB_SEARCH_CLI_PROFILE=research`).

## Command tree

The full command tree (as returned by `web-search-cli schema`) is:

| Path | Description |
| --- | --- |
| `schema` | Emit the planned CLI command tree as JSON. |
| `doctor` | Validate scaffold readiness without provider calls. |
| `recommend` | Recommend an existing CLI/MCP route from a natural-language task without executing it. |
| `getskill` | Print the bundled CLI skill markdown verbatim. |
| `feedback create` | Record a feedback entry (bug, requirement, suggestion, bad-output). |
| `feedback list` | List recorded feedback entries. |
| `feedback show` | Show details for a specific feedback entry by ID. |
| `feedback close` | Mark a feedback entry as closed. |
| `feedback transition` | Transition feedback status (`open`, `investigating`, `resolved`, `wontfix`). |
| `skills` | List registered skills or print one verbatim by name. |
| `inference describe` | Describe registered models, providers, and chains without secrets. |
| `inference validate` | Validate that chain references resolve to registered models/adapters. |
| `inference chain` | Inspect a single inference chain's configuration and provider details. |
| `search web` | Run the full multi-provider web search pipeline. |
| `search quick` | Run the Parallel AI-backed quick web search path. |
| `search academic` | Search scholarly sources and return deduplicated papers. |
| `search code` | Search public code, documentation, GitHub repositories, or Hugging Face Hub assets. |
| `content get` | Fetch one known URL with bounded windowing. |
| `content batch` | Fetch multiple URLs with a total content budget. |
| `links discover` | Discover links on a page or sitemap. |
| `links similar` | Find similar links to a known good URL (Composio). |
| `ai gemini` | Run a Gemini-grounded search with citations. |
| `ai grok` | Run a native xAI Grok live search with citations. |
| `youtube search` | Search YouTube videos via the SearXNG YouTube engine. |
| `youtube transcript` | Fetch a YouTube transcript with optional translation/formatting. |
| `analytics query` | Run a natural-language analytics question against DuckDB. |
| `analytics report` | Run a deterministic analytics report. |
| `sitemap generate` | Generate a sitemap with Tavily Map. |
| `experiments list` | List all experiments from the A/B config YAML. |
| `experiments enable` | Set an experiment status to `running`. |
| `experiments disable` | Set an experiment status to `paused`. |
| `experiments conclude` | Set an experiment status to `concluded` with a winning variant. |
| `experiments stats` | Show basic stats for an experiment. |
| `experiments create` | Scaffold a new experiment interactively or from a JSON config. |
| `reference tools` | Emit MCP-tool to CLI-command coverage. |
| `reference external-tools` | Emit companion CLI tools to invoke directly. |
| `server start` | Start the MCP server with the chosen transport. |
## Command reference
### `recommend`

Recommend an existing CLI/MCP route from a natural-language task. The command
returns structured route metadata, fallbacks, and decomposition guidance; it
never executes a command or calls a provider.

```powershell
web-search-cli recommend "Find current official docs for FastMCP middleware"
```


### `schema`

Emit the planned CLI command tree as JSON. No arguments.

```powershell
web-search-cli schema
```

### `doctor`

Validate scaffold readiness without provider calls. No arguments.

Checks reported: `package_importable`, `typer_importable`, `user_skill`,
`dev_skill`, `duckdb_cli` (optional), `phoenix_instrumentor` (optional),
`repo_root`.

```powershell
web-search-cli doctor
```

### `getskill`

Print the bundled CLI skill markdown verbatim. Use `--dev` to print the
developer skill (`skills/web-search-cli-dev/SKILL.md`) instead of the user
skill (`skills/web-search-cli/SKILL.md`).

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--dev` | bool | `False` | Print the developer skill. |

```powershell
web-search-cli getskill
web-search-cli getskill --dev
```

### `search web`

Run the full multi-provider web search pipeline (rewrite → multi-provider
search → RRF merge → rerank).

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` / `-q` | list[str] (required) | — | Search query text (repeat up to 4 times for multi-query search). |
| `--research-goal` | string (required) | — | Required search objective that drives the query policy layer. |
| `--rewrite` / `--no-rewrite` | bool | `True` | Run query rewrite/expansion before searching. |
| `--reranking-instructions` | string | — | Instructions for cross-encoder & LLM rerankers specifying what sites to prioritize/demote. |
| `--searxng-category` | list[str] | — | SearXNG categories; repeatable. |
| `--searxng-engine` | list[str] | — | SearXNG engines; repeatable. |
| `--searxng-language` | string | — | SearXNG language code (e.g. `en`). |
| `--searxng-pageno` | int | `1` | SearXNG page number. |
| `--searxng-time-range` | string | — | SearXNG time range (`day`, `week`, `month`, `year`). |
| `--searxng-safesearch` | int | — | SearXNG safesearch level (`0`–`2`). |
| `--site-filter` | list[str] | — | Restrict results to given sites; repeatable. |
| `--domain-filter` | list[str] | — | Restrict results to given domains; repeatable. |
| `--domain-boost` | list[str] | — | Domains to boost (move to front); repeatable. |
| `--domain-block` | list[str] | — | Domains to exclude (remove entirely); repeatable. |
| `--diagnostics` | bool | `False` | Include full pipeline diagnostics in the output under `_diagnostics`. |

```powershell
web-search-cli search web --query "function calling best practices 2026" --research-goal "find current best practices for LLM function calling"
web-search-cli search web --query "arxiv 2401.01234" --research-goal "locate and review this paper" --searxng-engine arxiv --searxng-engine google_scholar
web-search-cli search web --query "async python patterns" --research-goal "survey async patterns" --domain-boost docs.python.org --domain-block pinterest.com --diagnostics
```

### `search quick`

Run the Parallel AI-backed quick web search path.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--search-query` / `--query` | list[str] (required) | — | Keyword search query (3-6 words; repeat for 2-3 queries). |
| `--objective` / `--research-goal` | string (required) | — | Research goal — what you are trying to accomplish with this search. |

```powershell
web-search-cli search quick --query "latest openai announcements" --objective "track recent product releases"
```

### `search academic`

Search scholarly sources and return deduplicated papers.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Search query text. |
| `--limit` | int | `5` | Max papers to return (clamped 1–20). |
| `--source` | list[str] | — | Scholarly source(s); repeatable. |
| `--source-type` | string | — | Source type filter (`general`, `polish`, `archive`). |
| `--year-from` | int | — | Lower bound on publication year. |
| `--year-to` | int | — | Upper bound on publication year. |
| `--field-of-study` | list[str] | — | Field-of-study filter(s); repeatable. |
| `--venue` | string | — | Restrict to a specific venue. |
| `--open-access-only` / `--no-open-access-only` | bool | `False` | Only return open-access papers. |
| `--sort` | string | `relevance` | Sort order. |

```powershell
web-search-cli search academic --query "agentic rag" --year-from 2024 --open-access-only
```

### `search code`

Search public source code, implementation examples, documentation, GitHub repositories, or Hugging Face Hub assets. Use `--mode huggingface` for the exclusive semantic Hub provider; other modes retain their existing channel behavior.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Exact identifier, error signature, code query, or natural-language search. |
| `--research-goal` | string | — | Optional task context for rewriting and reranking. |
| `--repository` / `--repositories` | list[str] | — | GitHub `owner/name` scope; repeatable, max 25. |
| `--language` | string | — | Programming language qualifier. |
| `--path` | string | — | Repository path or glob filter. |
| `--filename` | string | — | Filename or filename pattern filter. |
| `--extension` | string | — | File extension filter. |
| `--regexp` / `--no-regexp` | bool | `False` | Enable regular-expression search where supported. |
| `--deep` / `--no-deep` | bool | `False` | Fetch bounded source windows and broaden repository discovery. |
| `--repo-name` | string | — | Repository discovery hint. |
| `--library-name` | string | — | Library/package discovery hint. |
| `--topic` | string | — | GitHub topic or ecosystem filter. |
| `--mode` | `code`, `docs`, `discovery`, `huggingface` | `code` | Select implementation, documentation, repository discovery, or semantic Hub asset focus. |
| `--huggingface-type` | `models`, `datasets`, `both` | `both` | Hub asset type for Hugging Face mode. |
| `--huggingface-sort-by` | string | `similarity` | Hub ranking: similarity, likes, downloads, trending, or updated. |
| `--huggingface-hybrid` | bool | `False` | Enable Hub hybrid lexical/semantic ranking. |

```powershell
web-search-cli search code --query "FastMCP tool registration" --repository "prefecthq/fastmcp"
web-search-cli search code --query "retry backoff" --language Python --path "src/" --deep
web-search-cli search code --query "MCP API reference" --library-name fastmcp --mode docs
```

### `content get`

Fetch one known URL with bounded windowing.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--url` | string (required) | — | URL to fetch. |
| `--char-offset` | int | `0` | Start offset in the extracted markdown. |
| `--char-length` | int | `20000` | Max characters to return from `--char-offset`. |
| `--ai-summary` / `--no-ai-summary` | bool | `False` | Include a detailed source-grounded Gemini summary. |
| `--focus-query` | string | — | Optional focus query for the summary. |
| `--include-metadata` / `--no-include-metadata` | bool | `True` | Include page metadata in the response. |
| `--include-links` / `--no-include-links` | bool | `False` | Include extracted links. |
| `--max-links` | int | `25` | Cap on extracted links when `--include-links` is set. |
| `--strip-selectors` | string | — | CSS selectors to strip before extraction (JSON-encoded list). |

```powershell
web-search-cli content get --url "https://example.com/post" --char-length 8000
web-search-cli content get --url "https://example.com/post" --ai-summary --focus-query "deployment steps"
```

### `content batch`

Fetch multiple URLs with a total content budget and bounded concurrency.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--url` | list[str] | — | URLs to fetch; repeatable. |
| `--cursor` | string | — | Opaque cursor to resume a prior batch run. |
| `--max-concurrency` | int | `4` | Max concurrent fetches. |
| `--per-item-char-length` | int | `12000` | Max characters per URL. |
| `--total-char-budget` | int | `120000` | Max total characters across the batch. |
| `--per-url-timeout-seconds` | float | `120.0` | Per-URL fetch timeout. |
| `--ai-summary` / `--no-ai-summary` | bool | `False` | Include a detailed source-grounded Gemini summary for each item. |
| `--focus-query` | string | — | Optional focus query for the summaries. |
| `--include-metadata` / `--no-include-metadata` | bool | `True` | Include page metadata. |
| `--include-links` / `--no-include-links` | bool | `False` | Include extracted links. |
| `--max-links` | int | `25` | Cap on extracted links per URL. |
| `--strip-selectors` | string | — | CSS selectors to strip before extraction (JSON-encoded list). |

```powershell
web-search-cli content batch --url "https://a.example/post" --url "https://b.example/post" --total-char-budget 60000
web-search-cli content batch --url "https://a.example/post" --url "https://b.example/post" --ai-summary --focus-query "API changes"
```

### `links discover`

Discover links on a page or sitemap.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--url` | string (required) | — | Page or sitemap URL. |
| `--max-links` | int | `100` | Max links to return. |
| `--include-external` / `--no-include-external` | bool | `True` | Include links to other domains. |
| `--same-domain-only` / `--no-same-domain-only` | bool | `False` | Restrict to the input's own domain. |
| `--strip-selectors` | string | — | CSS selectors to strip before extraction (JSON-encoded list). |

```powershell
web-search-cli links discover --url "https://example.com/sitemap.xml" --same-domain-only --max-links 200
```

### `links similar`

Find similar links to a known good URL (Composio `similarlinks`).

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--url` | string (required) | — | Known good URL. |
| `--num-results` | int | `5` | Number of similar links to return. |
| `--search-type` | string | `neural` | Composio search type. |
| `--category` | string | — | Optional Composio category. |
| `--include-domain` | list[str] | — | Domains to include; repeatable. |
| `--exclude-domain` | list[str] | — | Domains to exclude; repeatable. |

```powershell
web-search-cli links similar --url "https://docs.python.org/3/library/asyncio-task.html" --num-results 8
```

### `ai gemini`

Run a Gemini-grounded search with citations (uses Google Search grounding
via Gemini).

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Search query text. |
| `--structured-output` / `--no-structured-output` | bool | `False` | Request structured (JSON-schema) output. |
| `--research-goal` | string | — | Optional research goal. |

```powershell
web-search-cli ai gemini --query "what changed in python 3.13 asyncio"
```

### `ai grok`

Run a native xAI Grok live search with citations via the direct Responses API.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Search query text. |
| `--research-goal` | string | `""` | Research goal forwarded to the model. |
| `--model` | string | `grok-4.5` | Override the xAI Grok model id (e.g. `grok-4.5`). |
| `--num-results` | int | `5` | Number of search results to surface. |
| `--allowed-domain` | list[str] | — | Domains the model may cite; repeatable. |
| `--excluded-domain` | list[str] | — | Domains the model must not cite; repeatable. |
| `--timeout` | float | — | Request timeout in seconds. |

```powershell
web-search-cli ai grok --query "breaking news on regulation 2024-EU-AI-Act" --num-results 8
```

### `youtube search`

Search YouTube videos via the SearXNG YouTube engine.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Search query text. |
| `--num-results` | int | `5` | Number of videos to return. |

```powershell
web-search-cli youtube search --query "rust async tokio tutorial" --num-results 5
```

### `youtube transcript`

Fetch a YouTube transcript with optional language/translation/formatting.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--video-id-or-url` | string (required) | — | YouTube URL or video id. |
| `--language` | string | — | Preferred transcript language code (e.g. `en`). |
| `--translate-to` | string | — | Translate the transcript into this language code. |
| `--format` | `text` \| `timestamped` \| `json` | `text` | Transcript output format. |
| `--backend` | string | — | Transcript backend: `auto` (cascade), `ytdlp`, `api`. |

```powershell
web-search-cli youtube transcript --video-id-or-url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --format timestamped
web-search-cli youtube transcript --video-id-or-url "dQw4w9WgXcQ" --language en --backend ytdlp
```

### `analytics query`

Run a natural-language analytics question against the local DuckDB analytics
file (or MotherDuck if `--scope motherduck` is set).

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--question` | string (required) | — | Analytics question. |
| `--scope` | `local` \| `motherduck` | `local` | Analytics backend scope. |
| `--max-rows` | int | `100` | Max rows to return. |
| `--db-path` | string | — | Override the DuckDB file path. |

```powershell
web-search-cli analytics query --question "top 10 providers by error count in the last 7 days" --max-rows 25
```

### `analytics report`

Run a deterministic analytics report. Use `web-search-cli reference tools`
or the `analytics.queries.AVAILABLE_REPORTS` helper to list report names.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--report-name` | string (required) | — | Deterministic report name. |
| `--days` | int | `7` | Lookback window in days. |
| `--db-path` | string | — | Override the DuckDB file path. |

```powershell
web-search-cli analytics report --report-name "provider-performance" --days 14
```

### `sitemap generate`

Generate a sitemap using Tavily Map. Tavily Map supports natural-language
mapping instructions and regex path/domain filters.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--url` | string (required) | — | Website URL to map. |
| `--instructions` | string | — | Natural-language guidance for Tavily Map. |
| `--max-depth` | int | `1` | Traversal depth (Tavily-supported range `1`–`5`). |
| `--max-breadth` | int | `20` | Per-level breadth limit. |
| `--limit` | int | `50` | Maximum total URLs to return. |
| `--select-paths` | list[str] | — | Regex path filters; repeatable. |
| `--select-domains` | list[str] | — | Regex domain filters; repeatable. |
| `--exclude-paths` | list[str] | — | Regex path exclusions; repeatable. |
| `--exclude-domains` | list[str] | — | Regex domain exclusions; repeatable. |
| `--allow-external` / `--no-allow-external` | bool | `False` | Follow external links when true. |

```powershell
web-search-cli sitemap generate --url "https://docs.python.org/3/" --max-depth 2
web-search-cli sitemap generate --url "https://example.com/docs" --instructions "only API reference pages" --select-paths "/api/" --exclude-paths "/draft/"
```

### `feedback`

Record and manage project feedback entries stored in `feedback/{id}.json`.

#### `feedback create`

Create a new feedback entry.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--message` | string (required) | — | Feedback description message. |
| `--feedback-type` | string | `bug` | Feedback type (`bug`, `requirement`, `suggestion`, `bad-output`). |
| `--command-context` | string | — | Command or context where the issue occurred. |
| `--exit-code` | int | `0` | Exit code associated with the event. |

```powershell
web-search-cli feedback create --message "Search timed out on SearXNG" --feedback-type bug --command-context "search web"
```

#### `feedback list`

List recorded feedback entries with optional filtering.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--feedback-type` | string | — | Filter by feedback type. |
| `--status` | string | — | Filter by status (`open`, `investigating`, `resolved`, `wontfix`). |

```powershell
web-search-cli feedback list --status open
```

#### `feedback show`

Show full details for a feedback entry by ID.

| Arg | Type | Description |
| --- | --- | --- |
| `FEEDBACK_ID` | string (required) | Feedback ID to inspect (e.g. `001`). |

```powershell
web-search-cli feedback show 001
```

#### `feedback close`

Mark a feedback entry as closed.

| Arg | Type | Description |
| --- | --- | --- |
| `FEEDBACK_ID` | string (required) | Feedback ID to close (e.g. `001`). |

```powershell
web-search-cli feedback close 001
```

#### `feedback transition`

Transition feedback status.

| Arg | Type | Description |
| --- | --- | --- |
| `FEEDBACK_ID` | string (required) | Feedback ID to transition. |

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--status` | string (required) | — | Target status (`open`, `investigating`, `resolved`, `wontfix`). |

```powershell
web-search-cli feedback transition 001 --status resolved
```

### `skills`

List registered agent skills or output a skill's full markdown verbatim by name.

| Arg | Type | Description |
| --- | --- | --- |
| `NAME` | string (optional) | Skill name to display verbatim (e.g. `web-search-cli`, `getting-started`). |

```powershell
web-search-cli skills
web-search-cli skills getting-started
```

### `inference`

Inspect and validate the inference subsystem (models, adapters, chains).

#### `inference describe`

Describe registered models, providers, and chains without secrets.

```powershell
web-search-cli inference describe
```

#### `inference validate`

Validate that all chain references resolve to registered models and adapters.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--strict` / `--no-strict` | bool | `False` | Exit with non-zero status on validation errors. |

```powershell
web-search-cli inference validate --strict
```

#### `inference chain`

Inspect a single chain's configuration and provider details.

| Arg | Type | Description |
| --- | --- | --- |
| `NAME` | string (required) | Chain name to inspect (e.g. `fast_rewrite`). |

```powershell
web-search-cli inference chain fast_rewrite
```

### `experiments`

Manage A/B experiments backed by a YAML config file
(`experiments.yaml` or `AB_TESTING_CONFIG_PATH`).

#### `experiments list`

List all experiments with their status and variant configuration. No arguments.

```powershell
web-search-cli experiments list
```

#### `experiments enable`

Set an experiment status to `running`.

| Arg | Type | Description |
| --- | --- | --- |
| `EXPERIMENT_ID` | string (required) | Experiment ID to enable. |

```powershell
web-search-cli experiments enable my-query-rewrite-test
```

#### `experiments disable`

Set an experiment status to `paused`.

| Arg | Type | Description |
| --- | --- | --- |
| `EXPERIMENT_ID` | string (required) | Experiment ID to disable. |

```powershell
web-search-cli experiments disable my-query-rewrite-test
```

#### `experiments conclude`

Set an experiment status to `concluded` with a winning variant.

| Arg | Type | Description |
| --- | --- | --- |
| `EXPERIMENT_ID` | string (required) | Experiment ID to conclude. |

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--winner` | string (required) | — | Winning variant key. |

```powershell
web-search-cli experiments conclude my-query-rewrite-test --winner variant-b
```

#### `experiments stats`

Show basic stats for an experiment (status, variants, weights, winner).

| Arg | Type | Description |
| --- | --- | --- |
| `EXPERIMENT_ID` | string (required) | Experiment ID to show stats for. |

```powershell
web-search-cli experiments stats my-query-rewrite-test
```

#### `experiments create`

Scaffold a new experiment. In non-interactive mode (default), pass a JSON
config via `--config`. In interactive mode (`--non-interactive=false`),
prompts walk through experiment setup.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--config` | string | — | JSON string with experiment config (id, layer, hypothesis, primary_metric, traffic_pct, guardrail_metrics, variants). |

```powershell
web-search-cli experiments create --config '{"experiment_id":"rerank-test","layer":"reranking","status":"draft","hypothesis":"Bi-encoder first improves latency","primary_metric":"p95_latency_ms","traffic_pct":10,"variants":[{"variant_key":"control","weight":1,"description":"current pipeline"},{"variant_key":"bi-first","weight":1,"description":"bi-encoder before cross-encoder"}]}'
```

### `reference tools`

Emit the MCP-tool to CLI-command coverage matrix. Filter by capability
profile.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--profile` | `default` \| `research` \| `media` \| `diagnostic` \| `experimental` \| `full` | `full` | Capability profile to filter by. |

```powershell
web-search-cli reference tools --profile research
```

### `reference external-tools`

Emit companion CLI tools that should be invoked directly (DuckDB, Grafana
via WSL, Langfuse). No arguments.

```powershell
web-search-cli reference external-tools
```

### `server start`

Start the MCP server (`server.py:main`) with the chosen transport. The CLI
forwards a single transport flag plus optional host/port to the server
entry point. When multiple transport flags are set, the first matching one
in `--http`, `--sse`, `--stdio` order wins.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--http` / `--no-http` | bool | `False` | Run the server over HTTP. |
| `--sse` / `--no-sse` | bool | `False` | Run the server over Server-Sent Events. |
| `--stdio` / `--no-stdio` | bool | `True` | Run the server over stdio (default MCP transport). |
| `--host` | string | — | HTTP/SSE host (forwarded to the server). |
| `--port` | int | — | HTTP/SSE port (forwarded to the server). |

```powershell
web-search-cli server start --stdio
web-search-cli server start --http --host 127.0.0.1 --port 8000
```

## JSON envelope (success)

Every successful invocation writes a single JSON object to stdout shaped
like:

```json
{
  "schema_version": "1.0",
  "data": { "...command-specific payload..." },
  "meta": {
    "command": "<full command path>",
    "profile": "full",
    "duration_ms": 0,
    "generated_at": "2026-06-07T12:34:56Z"
  },
  "suggested_next": []
}
```

When `--debug` is set, `meta` also includes `"log_level": "DEBUG"` and
`"debug": true`.

## JSON envelope (error)

Errors are written as JSON to stderr and exit with a non-zero code from
`src/kindly_web_search_mcp_server/cli/exit_codes.py`:

| Code | Name | Typical cause |
| --- | --- | --- |
| 0 | `SUCCESS` | Command completed without error. |
| 1 | `INTERNAL_ERROR` | Unhandled exception. |
| 2 | `USAGE_ERROR` | Bad arguments or validation failure. |
| 3 | `NOT_FOUND` | Required resource missing (e.g. skill file, DB). |
| 4 | `AUTH_ERROR` | Missing/invalid credentials. |
| 5 | `CONFLICT` | Resource state conflict. |
| 6 | `RATE_LIMITED` | Provider rate limit hit. |
| 7 | `VALIDATION_ERROR` | Schema/contract validation failure. |
| 8 | `NETWORK_ERROR` | Transport-level network failure. |
| 9 | `SCHEMA_ERROR` | Output schema violation. |
| 10 | `PROVIDER_ERROR` | Upstream provider call failed. |
| 11 | `PERMISSION_ERROR` | Local permission denied. |
| 12 | `TIMEOUT` | Operation exceeded its time budget. |

Error envelope:

```json
{
  "error": {
    "kind": "tool_error",
    "message": "...",
    "hint": "...",
    "context": { "command": "search web", "exception_type": "..." }
  }
}
```

## Agent guidance

The guidance below is derived directly from the MCP tool docstrings
(`server.py`, `composio_tools.py`, `analytics/tools.py`),
the tool catalog annotations (`tools/catalog.py`), the seven MCP prompts
defined in `server.py`, and the `docs://workflow` resource.

### Tool routing matrix

Pick the command whose MCP counterpart best matches the task. The matrix
below mirrors the server's `_SEARCH_TOOL_ROUTING` prompt so the CLI and
MCP surfaces stay in sync.

| Task | CLI command | MCP tool | Why |
| --- | --- | --- | --- |
| Find URLs about a topic | `search web` | `web_search` | Lightweight results, multi-provider merge, `provider_count` agreement signal. |
| Public code or implementation examples | `search code` | `code_search` | Automatic lexical/symbol/regex/semantic/repository/documentation channels with typed hits and diagnostics. |
| Semantic Hugging Face model/dataset discovery | `search code --mode huggingface` | `code_search` | Hub summaries, filters, model metadata, semantic scores, and typed assets. |
| Quick factual answer with citations | `ai gemini` | `gemini_search` | Google-grounding, `[N]` inline citations, fast. |
| Web + X/Twitter with synthesis | `ai grok` | `grok_search` | AI-synthesized, real-time web and social data, native xAI search. |
| Scholarly papers with filters | `search academic` | `academic_search` | 6 sources (S2, ArXiv, PubMed, OpenAlex, CrossRef, CORE), field/venue/year filters. |
| Read one known URL | `content get` | `get_content` | 7-stage resolution (StackExchange → GitHub Issues/Discussions → Wikipedia → arXiv → HTTP → browser). |
| Read 3+ URLs with a budget | `content batch` | `batch_get_content` | Parallel fetch, `total_char_budget`, cursor continuation. |
| Expand a known URL into outgoing links | `links discover` | `discover_links` | Page/sitemap link discovery without body extraction. |
| Find videos | `youtube search` | `youtube_search` | SearXNG YouTube engine. |
| Extract video speech | `youtube transcript` | `youtube_transcript` | Timestamped or plain text, optional translation. |
| Quick synthesized answer (Parallel AI) | `search quick` | `quick_web_search` | Parallel AI-backed, fast keyword discovery. |
| Expand from a known good URL | `links similar` | `composio_similarlinks` | Neural similarity, include/exclude domain filters. |
| Local analytics question | `analytics query` | — | Native CLI analytics command (Guarded DuckDB query). |
| Deterministic analytics report | `analytics report` | — | Native CLI analytics report command (Fixed report catalog). |

### Query formulation

The `search web` command is the primary discovery path. Follow the rules
from the `web_search` docstring:

- **`--rewrite` (default `True`)**: Mistral expands the query for broader
  coverage. Use for normal discovery.
- **`--no-rewrite`**: exact-literal search. Use for stack traces, quoted
  error messages, URLs, package versions, hashes, UUIDs, CLI flags,
  function names, or other strings that must not be paraphrased.
- **`--research-goal` (required)**: a concise statement of the search
  objective. This drives the query policy layer (intent classification,
  provider selection, branch planning). Make it specific: "find current
  best practices for LLM function calling in 2026", not "function calling".
- **`--reranking-instructions`**: optional cross-encoder / LLM reranking guidance to boost or demote specific source types.
- **`--domain-boost`** moves matches to the front of results
  (`stackoverflow.com`, `github.com`). **`--domain-block`** removes them
  (`pinterest.com`, `quora.com`). Both support subdomain and path-aware
  matching.
- **Academic**: use `--year-from`/`--year-to`, `--venue` (e.g. `NeurIPS`),
  `--field-of-study` (e.g. `Computer Science`), `--open-access-only`.
- **YouTube transcript**: cloud IPs may be blocked — set
  `YOUTUBE_TRANSCRIPT_PROXY_URL` to a working proxy.

### Depth strategy

Mirror of the server's `_SEARCH_TOOL_ROUTING` depth block:

- **quick**: `ai gemini` or `search quick`. Skip content extraction
  unless really needed.
- **medium**: `search web` → `content batch` on the
  best 2–3 → `ai gemini` for synthesis.
- **deep**: `search web` (default rewrite) →
  `content batch` on the top 5 → `ai grok` on the refined query →
  `search academic` if scholarly sources are required.

### Result evaluation

From `_RESULT_EVALUATION_RULES`. Inspect the JSON envelope after every
`search web` or `search academic` call before fetching:

1. **`provider_count`**: how many configured providers returned this URL.
   2+ is a stronger signal; 0/missing is single-source (treat with
   lower confidence). The value comes through on the `web_search` output.
2. **Snippet quality**: prefer snippets that quote specific facts, code,
   dates, version numbers. Treat generic marketing text as low-signal.
   Domain hints: `github.com` → likely issue/PR, `stackoverflow.com` →
   Q&A, `docs.*` → official docs.
3. **Domain authority (heuristic)**: `.gov`/`.edu` and official docs
   sites are generally trustworthy; GitHub issues/PRs are high-signal
   for debugging; Medium/dev.to/personal blogs need cross-checks.

Decision rules after evaluating results:

- 3+ promising → `content batch` with an appropriate `total_char_budget`.
- 1–2 look good → `content get` on each; check `window.has_more` and
  continue with `char_offset = window.next_offset`.
- Off-topic → refine the query (different keywords, add domain terms)
  or call `ai gemini` for quick reorientation.
- Sparse (< 3 returned) → broaden: drop specific terms, switch to
  `--rewrite`.
- Thin snippets on otherwise promising URLs → `content get` first,
  then decide.
- Deep analysis needed → refine to ONE focused question, then
  `ai grok`.

### Pagination awareness

Both `content get` and `content batch` are paginated. Never assume the
returned window is the whole page.

- `content get` → check `data.window.has_more`. If `true`, call again
  with `data.window.next_offset` as `--char-offset`.
- `content batch` → check `data.has_more` and `data.cursor`. If `true`,
  call again with `--cursor <value>`. Per-URL timeout is
  `--per-url-timeout-seconds` (default 120s) and per-page cap is
  `--per-item-char-length` (default 12000).

### Iteration strategy (gap analysis)

From `_GAP_ANALYSIS_RULES`. After the first round, evaluate what is
missing before continuing or stopping.

Gap identification:

1. **Factual gaps**: claims, numbers, dates, API details that are
   unverified.
2. **Source gaps**: only one type of source (e.g. only blog posts, no
   official docs).
3. **Perspective gaps**: only one viewpoint (only author docs, no
   community critique).
4. **Recency gaps**: check dates in snippets or fetched content.
5. **Depth gaps**: any `has_more=true` on a fetched page — the rest of
   the page may hold answers.

Decomposition for follow-up rounds:

- **Aspect**: split the topic into sub-facets.
- **Perspective**: same question from different angles.
- **Refinement**: narrow with domain terms, version numbers, or date
  ranges from initial results.
- **Counter-query**: when results lean one way, explicitly search for
  opposing views or known issues.

Source triangulation:

- One source = interesting. Two independent agreeing sources = likely
  true. Three+ = well-established.
- If a claim only appears on one domain, flag it as unverified.
- Cross-check community sources (Reddit, HN) for real-world experience
  against official docs for API accuracy.

Termination criteria — stop when **all** apply:

- Three independent sources confirm the same finding.
- Two consecutive rounds produce no new information.
- Coverage minimum: official docs + GitHub issues + one community
  source.
- `provider_count` ≥ 3 on the key source URLs.
- Depth budget exhausted (quick → medium → deep) and gaps remain.

Breadth decay (each iteration narrower than the last):

- Round 1: broad discovery (`search web`, rewrite on).
- Round 2: targeted follow-up (2–3 refined `search web` queries).
- Round 3: pinpoint verification (1–2 precise queries, `--no-rewrite`).
- `links similar` on the best URL from round 1 to discover adjacent
  pages.
- If video content would help: `youtube search` → `youtube transcript`
  on the most relevant video.

### Academic deep-dive

From `_ACADEMIC_DEEP_DIVE_RULES`:

1. Start with `search academic`, not general web search.
2. Use `--year-from`/`--year-to`, `--venue`, `--field-of-study`,
   `--open-access-only` to narrow early.
3. Prefer exact paper titles, author names, and benchmark names in
   follow-up queries.
4. Check citation count, venue, and year before treating a paper as
   foundational.
5. Use `content get` on the abstract/HTML landing page or PDF URL only
   after selecting the most relevant papers.
6. Cross-check scholarly claims with at least two independent papers.
7. Separate survey/background papers from implementation/benchmark
   papers.
8. Stop broadening once the core papers are in hand, then deepen on
   methods, baselines, and limitations.

Provider notes for `search academic`:

- **Semantic Scholar**: 214M+ papers, rich metadata. Optional
  `S2_API_KEY` unlocks 100 RPS (vs shared 1 RPS).
- **arXiv**: 2.5M+ CS/Physics/Math preprints, no auth.
- **OpenAlex**: 250M+ works; polite pool benefits from a contact email.
- **CrossRef**: DOI enrichment, citation counts, bibliographic data.
- **PubMed**: 35M+ biomedical citations; optional `NCBI_API_KEY` for
  10 RPS.
- **CORE**: open-access full-text, requires `CORE_API_KEY`.
- Results are deduplicated by DOI, ArXiv ID, PubMed ID, or title match.

### Video research

From `_VIDEO_RESEARCH_RULES`:

1. Use `youtube search` to discover candidate videos first.
2. Rank candidates by title specificity, channel authority, and likely
   transcript usefulness.
3. Use `youtube transcript` only on the best candidate videos.
4. Prefer transcript evidence over video title/description alone.
5. If a transcript is long, summarise the key sections and note where
   deeper follow-up is needed.
6. Cross-check tutorial claims against official docs or source code
   when accuracy matters.
7. If transcripts are unavailable or weak, fall back to standard
   web/document sources.

### Source triage

From `_SOURCE_TRIAGE_RULES`:

1. Official docs and vendor references for API behaviour and versioned
   contracts.
2. GitHub issues/PRs for real bugs, migrations, and edge cases.
3. Papers for scholarly or benchmark claims.
4. Community sources for practitioner experience, not as sole proof of
   correctness.
5. Prefer sources with clear dates, concrete examples, and direct
   evidence.
6. Flag single-source claims as provisional until corroborated.
7. When sources disagree, prefer the newer and more authoritative one,
   and note the conflict explicitly.

### Cost and rate-limit awareness

Some commands are explicitly marked as expensive or non-idempotent in
`tools/catalog.py`:

  Default cost: Grok 4.5 tokens + native xAI search tool
  usage. Requires `XAI_API_KEY`.
- `content batch` is bounded by `BATCH_GET_CONTENT_MAX_URLS`
  (default 30) and `BATCH_TOTAL_CHAR_BUDGET_MAX` (default
  300 000) server-side.

### Capability profiles

`--profile` controls which MCP tools the server exposes. The CLI uses
the same profile set (`src/kindly_web_search_mcp_server/tools/profiles.py`).
The default for the CLI global option is `full`.

| Profile | Tools exposed |
| --- | --- |
| `default` | `web_search`, `get_content`, `batch_get_content`, `discover_links` |
| `research` | `default` + `gemini_search`, `grok_search`, `academic_search`, `quick_web_search`, `composio_similarlinks` |
| `media` | `default` + `youtube_search`, `youtube_transcript` |
| `diagnostic` | `default` |
| `experimental` | All non-`default` tools |
| `full` | All tools (CLI default) |

Run `web-search-cli reference tools --profile <name>` to see the exact
coverage.

### MCP prompts (for MCP clients)

The server exposes three reusable prompts. CLI consumers can mirror
their intent by chaining the commands listed below.

| Prompt | Description | Suggested CLI chain |
| --- | --- | --- |
| `web_search_workflow` | Route quick, medium, or deep web research with focus-aware guidance. | `search quick` or `search web` → `content get` / `content batch` |
| `query_refinement` | Generate broaden, pinpoint, and decompose variants after sparse or off-topic results. | refine one query → `search web` |
| `research_methodology` | Reference the full decomposition, source evaluation, iteration, and termination workflow. | `search web` → `content batch` → `ai gemini` or `search academic` |


### MCP resources (read-only context)

The server exposes these read-only resources. The CLI mirrors the
operationally useful ones via commands; the rest are surfaced by the
server's resource interface.

| URI | Purpose | CLI equivalent |
| --- | --- | --- |
| `status://providers` | Which search providers are configured and their health state. | `web-search-cli doctor` (partial) |
| `status://features` | Server feature flags (rewrite, reranking, caches, timeouts). | `web-search-cli doctor` (partial) |
| `docs://workflow` | Full discovery → extraction → synthesis workflow text. | This skill file's "Agent guidance" section. |
| `settings://public` | Public runtime settings with secrets removed. | `web-search-cli reference tools` |
| `analytics://reports/{report_name}` | Parameterised report from the deterministic catalog. | `web-search-cli analytics report --report-name <name> --days <n>` |

### When to use / when not to use (per tool)

Quick "use this, not that" cheatsheet distilled from each tool's
docstring's `When to use` and `When not to use` blocks.

- **`search web`**: default for discovery. Use `--no-rewrite` for exact
  literals only. Don't use when you already have a specific URL — go
  to `content get`.
- **`search quick`**: for a Parallel AI-backed synthesised answer with
  citations. Don't use for paywalled or private content (not indexed).
- **`search academic`**: scholarly sources only. Don't use for general
  web questions; don't fetch full PDFs before selecting papers.
- **`content get`**: you already have a URL (from the user or
  `search web`). Don't use it for discovery.
- **`content batch`**: 3+ URLs. Don't use for one URL; don't use
  before discovery.
- **`links discover`**: have a URL, want outbound links. Don't use it
  to read article text or to discover the starting URL.
- **`links similar`**: have one known-good URL and want adjacent pages.
- **`ai gemini`**: need a quick grounded answer with `[N]` citations.
  Don't use it to compare multiple web pages — use `search web` for
  that.
- **`ai grok`**: need both web and X/Twitter in one synthesised answer.
  Don't use for raw URL lists or when you already have URLs.
- **`youtube search`**: discover videos. Don't use to read content
  (transcript instead) or for general web search.
- **`youtube transcript`**: video is captioned and public. Don't use
  it on private/age-restricted/disabled-transcript videos.
- **`analytics query`**: natural-language question against local
  DuckDB (or MotherDuck if `--scope motherduck`). Guarded by an
  allowlist.
- **`analytics report`**: run a known report name with `--days` window.
  Run `web-search-cli reference tools --profile diagnostic` to list
  available report names.

## Common usage patterns

Discover the command surface, then run a multi-provider search and fetch
the top result:

```powershell
web-search-cli schema
web-search-cli search web --query "function calling best practices 2026" --research-goal "find current best practices for LLM function calling"
web-search-cli content get --url "<top-result-url>" --char-length 8000
```

Inspect provider health, then ask the analytics backend for the top error
sources:

```powershell
web-search-cli doctor
web-search-cli analytics report --report-name "provider-performance" --days 7
```

Enable debug logging to diagnose a slow search:

```powershell
web-search-cli --debug search web --query "test query" --research-goal "diagnose latency" --diagnostics
```

## Environment

The CLI loads `.env` from the repository root and the current working
directory via `python-dotenv` (see `cli/bootstrap.py`). At minimum, set one
search-provider credential:

```powershell
$env:SEARXNG_BASE_URL = "http://localhost:8080"   # or TAVILY_API_KEY, BRAVE_API_KEY, JINA_API_KEY
$env:GITHUB_TOKEN = "..."                          # recommended for GitHub Issue/Discussion extraction
```

Optional for advanced features:

```powershell
$env:MISTRAL_API_KEY = "..."                       # query rewrite
$env:GEMINI_API_KEY = "..."                        # ai gemini
$env:XAI_API_KEY = "..."                           # ai grok (native xAI Responses API)
$env:PARALLEL_API_KEY = "..."                      # search quick (Parallel AI)
$env:OPENROUTER_API_KEY = "..."                    # OpenRouter rerank / rankllm
```

## Implementation pointers

- Entry point: `src/kindly_web_search_mcp_server/cli/app.py`
- Commands: `src/kindly_web_search_mcp_server/cli/commands/`
- Services: `src/kindly_web_search_mcp_server/cli/services/`
- JSON envelope: `src/kindly_web_search_mcp_server/cli/output.py`
- Errors: `src/kindly_web_search_mcp_server/cli/errors.py`
- Exit codes: `src/kindly_web_search_mcp_server/cli/exit_codes.py`
- Tool coverage matrix: `src/kindly_web_search_mcp_server/cli/reference_data.py`
- Skill paths: `src/kindly_web_search_mcp_server/cli/skill_paths.py`
- Design spec: `plans/web-search-cli-native-typer-design-2026-06-07.md`