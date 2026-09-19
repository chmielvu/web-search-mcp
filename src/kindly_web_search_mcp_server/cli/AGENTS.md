<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-19 | Last verified: 2026-09-19 -->

# AGENTS.md - CLI (web-search-cli)

Typer CLI for the MCP server. ALL CLI invocations MUST use `uv run web-search-cli`.

## Structure

```
cli/
├── app.py                   # Typer app with 18 registered command groups
├── commands/                # Command registration modules
│   ├── schema.py / doctor.py / getskill.py / skills.py
│   ├── feedback.py / reference.py / jobs.py / results.py
│   ├── research.py / search.py / content.py / links.py
│   ├── inference.py / ai.py / youtube.py / analytics.py
│   ├── server.py / sitemap.py
└── services/                # Shared service adapters and local stores
    ├── search_web.py / quick_search.py / academic.py
    ├── content.py / crawl.py / link_tools.py / ai.py
    ├── youtube.py / deep_research.py / sitemap.py
    ├── research_collect.py / jobs.py / results.py / search_runs.py
    ├── input.py / files.py
```

## Current Behavior

- `app.py` wires all commands into a JSON-first CLI. Standard command output is
  JSON to stdout; `--brief`, `--version`, skill markdown, and `--raw` emit
  plain text or raw value lines by design.
- Global flags: `--brief`, `--help`, `--version`, `--human`, `--agent`,
  `--dry-run`, `--quiet`, `--fields`, `--raw`, `--log-level`, `--log-format`,
- No implicit confirmation flag is supported; mutating commands fail closed
  rather than prompting.
- `--human` emits indented JSON with data plus command metadata; `--agent`
  explicitly selects the standard structured envelope. If both are supplied,
  `--human` wins.
- `--quiet` (`-q`) suppresses inline `rules`, `skills`, and `feedback`;
  `--raw` emits bare values; `--fields` projects selected response fields.
- Every structured failure is emitted to stderr with a stable error kind,
  message, hint, context, and non-zero exit code. No command prompts.
- `content crawl` exposes the bounded `crawl_web` MCP capability, and
  `research deep --no-wait` submits a local idempotent job.
- Inline context is sourced from the root `agent/` rules and
  `skills/web-search-cli/` artifacts. `feedback create/list/show/close/transition`
  stores the local feedback loop under `feedback/{id}.json`.
- Content commands use `--ai-summary/--no-ai-summary` (default disabled) for
  the detailed source-grounded Gemini summary; the former `--summary-mode`
  option is removed.

## Commands

```bash
uv run web-search-cli schema
uv run web-search-cli doctor
uv run web-search-cli --brief
uv run web-search-cli getskill
uv run web-search-cli skills web-search-cli
uv run web-search-cli feedback create --message "..." --type bug
uv run web-search-cli feedback list
uv run web-search-cli reference tools --profile full
uv run web-search-cli search quick --mode youtube --query "query"
uv run web-search-cli search web --query "query" --research-goal "goal"
uv run web-search-cli content fetch --url https://example.com
uv run web-search-cli content crawl --url https://example.com --max-pages 5
uv run web-search-cli research deep --query "topic" --no-wait
uv run web-search-cli jobs get <job-id>
uv run web-search-cli links discover --url https://example.com
uv run web-search-cli ai grok --query "query" --research-goal "goal"
uv run web-search-cli youtube transcript --video-id-or-url <video-id>
uv run web-search-cli youtube channel --channel <channel-id>
uv run web-search-cli analytics query --question "What happened?"
uv run web-search-cli sitemap generate --url https://example.com
```

## Testing

Tests are frozen for this experimental application. Do not run, modify, or
unfreeze tests unless explicitly instructed. Permitted verification commands
are `uv run --no-sync ruff check src/`, `uv run --no-sync ruff format --check
src/`, `uv run --no-sync ty check src`, and focused non-provider CLI smoke
commands.
### Recent Changes (2026-09-06)
- `content fetch` now exposes only the unified public result fields and accepts
  `--include-links`, `--ai-summary`, `--focus-query`, offset, and cursor options;
  obsolete metadata/link-tuning flags are rejected.
