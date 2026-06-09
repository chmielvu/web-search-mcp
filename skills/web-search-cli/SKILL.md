---
name: web-search-cli
description: JSON-first native Typer CLI for the web-search-mcp server. Use when an agent needs to discover, fetch, or analyse web content from a shell or scripted context, or to validate CLI readiness, inspect the command schema, or look up MCP-tool to CLI-command coverage.
---

# web-search-cli

The `web-search-cli` is the native, JSON-first command-line surface for the
Kindly Web Search MCP server. It mirrors the MCP tools exposed by
`server.py` and adds operational commands (`schema`, `doctor`, `getskill`,
`reference`, `server`) that are only meaningful outside of MCP.

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
| `--agent` | bool | `True` | Optimise output for agent consumers (no extra noise, JSON-first). |
| `--human` | bool | `False` | Optimise output for a human at a terminal. |
| `--quiet` / `-q` | bool | `False` | Suppress non-essential diagnostics. |
| `--profile` | string | `full` | Active capability profile: `default`, `research`, `media`, `diagnostic`, `experimental`, `full`. |
| `--log-level` | string | `error` | Log level for stderr diagnostics. |
| `--non-interactive` | bool | `True` | Disallow interactive prompts. |

## Command tree

The full command tree (as returned by `web-search-cli schema`) is:

| Path | Description |
| --- | --- |
| `schema` | Emit the planned CLI command tree as JSON. |
| `doctor` | Validate scaffold readiness without provider calls. |
| `getskill` | Print the bundled CLI skill markdown verbatim. |
| `search web` | Run the full multi-provider web search pipeline. |
| `search quick` | Run the Composio/Exa-backed quick web search path. |
| `search academic` | Search scholarly sources and return deduplicated papers. |
| `content get` | Fetch one known URL with bounded windowing. |
| `content batch` | Fetch multiple URLs with a total content budget. |
| `links discover` | Discover links on a page or sitemap. |
| `links similar` | Find similar links to a known good URL (Composio). |
| `images search` | Run an image search via Composio. |
| `ai gemini` | Run a Gemini-grounded search with citations. |
| `ai perplexity` | Run a Perplexity Sonar search with citations. |
| `ai grok` | Run a Grok (OpenRouter) live search with citations. |
| `agent research` | Run an agentic, multi-step web research session. |
| `youtube search` | Search YouTube videos via the SearXNG YouTube engine. |
| `youtube transcript` | Fetch a YouTube transcript with optional translation/formatting. |
| `analytics query` | Run a natural-language analytics question against DuckDB. |
| `analytics report` | Run a deterministic analytics report. |
| `reference tools` | Emit MCP-tool to CLI-command coverage. |
| `reference external-tools` | Emit companion CLI tools to invoke directly. |
| `server start` | Start the MCP server with the chosen transport. |

## Command reference

### `schema`

Emit the planned CLI command tree as JSON. No arguments.

```powershell
web-search-cli schema
```

### `doctor`

Validate scaffold readiness without provider calls. No arguments.

Checks reported: `package_importable`, `typer_importable`, `user_skill`,
`dev_skill`, `duckdb_cli` (optional), `langfuse_cli` (optional), `repo_root`.

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
| `--query` | string (required) | — | Search query text. |
| `--num-results` | int | `5` | Number of results to return. |
| `--rewrite` / `--no-rewrite` | bool | `True` | Run query rewrite/expansion before searching. |
| `--provider` | list[str] | — | Restrict to specific provider(s); repeatable. |
| `--research-goal` | string | — | Optional research goal used by the query policy layer. |
| `--result-offset` | int | `0` | Skip the first N merged results. |
| `--searxng-category` | list[str] | — | SearXNG categories; repeatable. |
| `--searxng-engine` | list[str] | — | SearXNG engines; repeatable. |
| `--searxng-language` | string | — | SearXNG language code (e.g. `en`). |
| `--searxng-pageno` | int | `1` | SearXNG page number. |
| `--searxng-time-range` | string | — | SearXNG time range (`day`, `week`, `month`, `year`). |
| `--searxng-safesearch` | int | — | SearXNG safesearch level (`0`–`2`). |
| `--site-filter` | list[str] | — | Restrict results to given sites; repeatable. |
| `--domain-filter` | list[str] | — | Restrict results to given domains; repeatable. |

```powershell
web-search-cli search web --query "function calling best practices 2026" --num-results 8
web-search-cli search web --query "arxiv 2401.01234" --searxng-engine arxiv --searxng-engine google_scholar
```

### `search quick`

Run the Composio/Exa-backed quick web search path.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Search query text. |

```powershell
web-search-cli search quick --query "latest openai announcements"
```

### `search academic`

Search scholarly sources and return deduplicated papers.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Search query text. |
| `--limit` | int | `5` | Max papers to return. |
| `--source` | list[str] | — | Scholarly source(s); repeatable. |
| `--year-from` | int | — | Lower bound on publication year. |
| `--year-to` | int | — | Upper bound on publication year. |
| `--field-of-study` | list[str] | — | Field-of-study filter(s); repeatable. |
| `--venue` | string | — | Restrict to a specific venue. |
| `--open-access-only` / `--no-open-access-only` | bool | `False` | Only return open-access papers. |
| `--sort` | string | `relevance` | Sort order. |

```powershell
web-search-cli search academic --query "agentic rag" --year-from 2024 --open-access-only
```

### `content get`

Fetch one known URL with bounded windowing.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--url` | string (required) | — | URL to fetch. |
| `--char-offset` | int | `0` | Start offset in the extracted markdown. |
| `--char-length` | int | `20000` | Max characters to return from `--char-offset`. |
| `--summary-mode` | `none` \| `brief` \| `detailed` | `none` | Optional summary level. |
| `--focus-query` | string | — | Optional focus query for the summary. |
| `--include-metadata` / `--no-include-metadata` | bool | `True` | Include page metadata in the response. |
| `--include-links` / `--no-include-links` | bool | `False` | Include extracted links. |
| `--max-links` | int | `25` | Cap on extracted links when `--include-links` is set. |
| `--strip-selectors` | string | — | CSS selectors to strip before extraction (JSON-encoded list). |

```powershell
web-search-cli content get --url "https://example.com/post" --char-length 8000
web-search-cli content get --url "https://example.com/post" --summary-mode brief --focus-query "deployment steps"
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
| `--include-metadata` / `--no-include-metadata` | bool | `True` | Include page metadata. |
| `--include-links` / `--no-include-links` | bool | `False` | Include extracted links. |
| `--max-links` | int | `25` | Cap on extracted links per URL. |
| `--strip-selectors` | string | — | CSS selectors to strip before extraction (JSON-encoded list). |

```powershell
web-search-cli content batch --url "https://a.example/post" --url "https://b.example/post" --total-char-budget 60000
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

### `images search`

Run an image search via Composio.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Image search query. |
| `--num-results` | int | `10` | Number of images to return. |
| `--page` | int | `0` | Pagination page index. |

```powershell
web-search-cli images search --query "kindly logo" --num-results 5
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

### `ai perplexity`

Run a Perplexity Sonar search with citations (via the Pollinations gateway).

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Search query text. |
| `--depth` | `normal` \| `deep` | `normal` | Sonar reasoning depth. |
| `--research-goal` | string | — | Optional research goal. |

```powershell
web-search-cli ai perplexity --query "compare sqlite vs duckdb for embedded analytics" --depth deep
```

### `ai grok`

Run a Grok (OpenRouter) live search with citations.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Search query text. |
| `--research-goal` | string | `""` | Research goal forwarded to the model. |
| `--model` | string | — | Override the OpenRouter model id. |
| `--num-results` | int | `5` | Number of search results to surface. |
| `--allowed-domain` | list[str] | — | Domains the model may cite; repeatable. |
| `--excluded-domain` | list[str] | — | Domains the model must not cite; repeatable. |
| `--timeout` | float | — | Request timeout in seconds. |

```powershell
web-search-cli ai grok --query "breaking news on regulation 2024-EU-AI-Act" --num-results 8
```

### `agent research`

Run an agentic, multi-step web research session using the local
`agentic_web_research` runner.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--query` | string (required) | — | Research query text. |
| `--research-goal` | string | — | Optional research goal. |
| `--session-id` | string | — | Resume an existing session by id. |
| `--depth` | `quick` \| `normal` \| `deep` | `normal` | Research depth. |

```powershell
web-search-cli agent research --query "what are the tradeoffs of pgvector vs lance db" --depth deep
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

```powershell
web-search-cli youtube transcript --video-id-or-url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --format timestamped
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
web-search-cli analytics report --report-name "provider_health" --days 14
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
(`server.py`, `composio_tools.py`, `agent/mcp.py`, `analytics/tools.py`),
the tool catalog annotations (`tools/catalog.py`), the seven MCP prompts
defined in `server.py`, and the `docs://workflow` resource.

### Tool routing matrix

Pick the command whose MCP counterpart best matches the task. The matrix
below mirrors the server's `_SEARCH_TOOL_ROUTING` prompt so the CLI and
MCP surfaces stay in sync.

| Task | CLI command | MCP tool | Why |
| --- | --- | --- | --- |
| Find URLs about a topic | `search web` | `web_search` | Lightweight results, multi-provider merge, `provider_count` agreement signal. |
| Quick factual answer with citations | `ai gemini` | `gemini_search` | Google-grounding, `[N]` inline citations, fast. |
| Web + X/Twitter with synthesis | `ai grok` | `grok_search` | AI-synthesized, real-time web and social data, native xAI search. |
| Deep reasoning across many sources | `ai perplexity` | `perplexity_search` | AI-synthesized, expensive — refine the query first. |
| Scholarly papers with filters | `search academic` | `academic_search` | 6 sources (S2, ArXiv, PubMed, OpenAlex, CrossRef, CORE), field/venue/year filters. |
| Read one known URL | `content get` | `get_content` | 7-stage resolution (StackExchange → GitHub Issues/Discussions → Wikipedia → arXiv → HTTP → browser). |
| Read 3+ URLs with a budget | `content batch` | `batch_get_content` | Parallel fetch, `total_char_budget`, cursor continuation. |
| Expand a known URL into outgoing links | `links discover` | `discover_links` | Page/sitemap link discovery without body extraction. |
| Find videos | `youtube search` | `youtube_search` | SearXNG YouTube engine. |
| Extract video speech | `youtube transcript` | `youtube_transcript` | Timestamped or plain text, optional translation. |
| Quick synthesized answer (Exa) | `search quick` | `quick_web_search` | Exa-backed, lighter than Perplexity. |
| Expand from a known good URL | `links similar` | `composio_similarlinks` | Neural similarity, include/exclude domain filters. |
| Image search | `images search` | `composio_image_search` | Returns URLs + metadata, not bytes. |
| Multi-step agentic research | `agent research` | `agentic_web_research` | ReAct agent that picks tools itself; experimental, not idempotent. |
| Local analytics question | `analytics query` | `analytics_query` | Guarded, allowlisted DuckDB query. |
| Deterministic analytics report | `analytics report` | `analytics_report` | Fixed report catalog with `--days` window. |

### Query formulation

The `web_search` command is the primary discovery path. Follow the rules
from the `web_search` docstring:

- **`--rewrite` (default `True`)**: Mistral expands the query for broader
  coverage. Use for normal discovery.
- **`--no-rewrite`**: exact-literal search. Use for stack traces, quoted
  error messages, URLs, package versions, hashes, UUIDs, CLI flags,
  function names, or other strings that must not be paraphrased.
- **`--num-results`**: 3 for fast existence checks, 5 (default) for
  standard coverage, 7 for broad coverage, max 10. Results are
  diversity-pruned, so 5–7 already gives good breadth.
- **`--provider`**: standard providers (`searxng`, `ddg`, `gemini`) fire
  automatically when configured. Request `tavily`/`brave`/`jina`
  explicitly. Available: `searxng`, `ddg`, `tavily`, `brave`, `jina`,
  `gemini`, `composio_llm_search`.
- **`--domain-boost`** moves matches to the front of results
  (`stackoverflow.com`, `github.com`). **`--domain-block`** removes them
  (`pinterest.com`, `quora.com`). Both support subdomain and path-aware
  matching.
- **Academic**: use `--year-from`/`--year-to`, `--venue` (e.g. `NeurIPS`),
  `--field-of-study` (e.g. `Computer Science`), `--open-access-only`.
- **YouTube transcript**: cloud IPs may be blocked — set
  `KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL` to a working proxy.

### Depth strategy

Mirror of the server's `_SEARCH_TOOL_ROUTING` depth block:

- **quick**: `ai gemini` or `search quick`. Skip content extraction
  unless really needed.
- **medium**: `search web` (`--num-results 5`) → `content batch` on the
  best 2–3 → `ai gemini` for synthesis.
- **deep**: `search web` (`--num-results 7`, default rewrite) →
  `content batch` on the top 5 → `ai perplexity` on the refined query →
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
  `ai perplexity`.

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

- Round 1: broad discovery (`search web --num-results 5-7`, rewrite on).
- Round 2: targeted follow-up (2–3 refined queries, `--num-results 3`).
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
  `KINDLY_S2_API_KEY` unlocks 100 RPS (vs shared 1 RPS).
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

- `ai perplexity` is **expensive** and rate-limited. The first call
  returns a steering message with query-writing best practices; refine
  the query and retry.
- `ai grok` is **expensive** and **not idempotent** (live web + X data).
  Default cost: Grok 4.3 tokens ($1.25/$2.50 per 1M) + search tool
  usage. Requires `OPENROUTER_API_KEY`.
- `agent research` is **experimental** and **not idempotent**. Use only
  for multi-step, open-ended research.
- `content batch` is bounded by `KINDLY_BATCH_GET_CONTENT_MAX_URLS`
  (default 30) and `KINDLY_BATCH_TOTAL_CHAR_BUDGET_MAX` (default
  300 000) server-side.

### Capability profiles

`--profile` controls which MCP tools the server exposes. The CLI uses
the same profile set (`src/kindly_web_search_mcp_server/tools/profiles.py`).
The default for the CLI global option is `full`.

| Profile | Tools exposed |
| --- | --- |
| `default` | `web_search`, `get_content`, `batch_get_content`, `discover_links` |
| `research` | `default` + `gemini_search`, `perplexity_search`, `grok_search`, `academic_search`, `quick_web_search`, `composio_similarlinks`, `agentic_web_research` |
| `media` | `default` + `composio_image_search`, `youtube_search`, `youtube_transcript` |
| `diagnostic` | `default` + `analytics_query`, `analytics_report` |
| `experimental` | All non-`default` tools (including `agentic_web_research`) |
| `full` | All tools (CLI default) |

Run `web-search-cli reference tools --profile <name>` to see the exact
coverage.

### MCP prompts (for MCP clients)

The server also exposes eight reusable prompts. CLI consumers can mirror
their intent by chaining the commands listed below.

| Prompt | Description | Suggested CLI chain |
| --- | --- | --- |
| `plan_web_research` | Plan research approach: choose tool, formulate queries, set depth. Use **before** any search. | `reference tools` → `search web` |
| `evaluate_web_results` | Assess result quality and decide next action. Use **after** `search web` or `search academic`. | `content get` (or `content batch`) on best URLs |
| `research_gap_analysis` | Identify what's missing, decompose remaining questions, plan next iteration. | Round 2 `search web` with refined terms |
| `suggest_tool` | Recommend the best tool(s) and parameters for a task. | `reference tools --profile ...` |
| `research_workflow` | Full discovery → extraction → synthesis workflow. | `search web` → `content batch` → `ai gemini` |
| `academic_deep_dive` | Scholarly research pass with `search academic` first. | `search academic` → `content get` on selected papers |
| `video_research` | YouTube-first research. | `youtube search` → `youtube transcript` |
| `source_triage` | Decide which sources are authoritative enough to fetch or cite. | `links similar` + `content get` |

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
| `cache://stats` | Cache topology and limits for exact/page/result-memory layers. | n/a (server-side only) |
| `analytics://schema` | Analytics tables/views catalog. | `web-search-cli reference tools --profile diagnostic` |
| `analytics://candidate-survival` | Default candidate-survival report (7 days). | `web-search-cli analytics report --report-name candidate-survival --days 7` |
| `analytics://cache-hit-rates` | Default cache-hit-rate report (7 days). | `web-search-cli analytics report --report-name cache-hit-rates --days 7` |
| `analytics://reports/{report_name}{?days}` | Parameterised report from the deterministic catalog. | `web-search-cli analytics report --report-name <name> --days <n>` |

### When to use / when not to use (per tool)

Quick "use this, not that" cheatsheet distilled from each tool's
docstring's `When to use` and `When not to use` blocks.

- **`search web`**: default for discovery. Use `--no-rewrite` for exact
  literals only. Don't use when you already have a specific URL — go
  to `content get`.
- **`search quick`**: for an Exa-backed synthesised answer with
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
- **`images search`**: image URLs/metadata only. URL accessibility and
  licensing must be verified.
- **`ai gemini`**: need a quick grounded answer with `[N]` citations.
  Don't use it to compare multiple web pages — use `search web` for
  that.
- **`ai perplexity`**: need AI synthesis across many sources. Don't use
  it for browsing specific URLs (use `search web` + `content get`) and
  refine the query first.
- **`ai grok`**: need both web and X/Twitter in one synthesised answer.
  Don't use for raw URL lists or when you already have URLs.
- **`agent research`**: multi-step open-ended research where the agent
  should pick tools itself. Experimental and not idempotent.
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
web-search-cli search web --query "function calling best practices 2026" --num-results 5
web-search-cli content get --url "<top-result-url>" --char-length 8000
```

Inspect provider health, then ask the analytics backend for the top error
sources:

```powershell
web-search-cli doctor
web-search-cli analytics report --report-name "provider_health" --days 7
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
$env:KINDLY_GEMINI_API_KEY = "..."                 # ai gemini
$env:POLLINATIONS_API_KEY = "..."                  # ai perplexity
$env:OPENROUTER_API_KEY = "..."                    # ai grok
$env:KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL = "..."   # youtube transcript from cloud IPs
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
