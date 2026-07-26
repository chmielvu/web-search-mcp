# AGENTS.md - CLI (web-search-cli)

Typer CLI for the MCP server. ALL CLI invocations MUST use `uv run web-search-cli`.

## Structure

```
cli/
├── app.py                   # Typer app with 15 command groups
├── commands/                # Command registration modules
│   ├── schema.py            # schema command
│   ├── doctor.py            # doctor command
│   ├── getskill.py          # getskill command
│   ├── skills.py            # skills command (list and view markdown)
│   ├── feedback.py          # feedback command (file auto-reports in feedback/{id}.json)
│   ├── reference.py         # reference commands
│   ├── search.py            # search commands
│   ├── content.py           # content commands
│   ├── links.py             # link-discovery commands
│   ├── ai.py                # AI/synthesis commands
│   ├── youtube.py           # YouTube commands
│   ├── analytics.py         # analytics query/report commands
│   ├── experiments.py       # A/B experiment management
│   ├── server.py            # server/launch helpers
│   └── sitemap.py           # sitemap generate
└── services/                # Shared service adapters
    ├── search_web.py        # Web search
    ├── quick_search.py      # Quick search
    ├── content.py           # Content fetch
    ├── content_batch.py     # Batch content
    ├── link_tools.py        # Link discovery
    ├── ai.py                # AI answers
    ├── academic.py          # Academic search
    ├── youtube.py           # YouTube
    └── sitemap.py           # Sitemap
```

## Current Behavior

- `app.py` wires all commands into a JSON-first CLI. Standard command output is JSON to stdout (`--brief`, `--version`, skill markdown, and `--raw` mode emit plain text / raw value lines by design).
- Global reserved flags: `--brief`, `--help`, `--version`, `--yes`, `--dry-run`, `--quiet`, `--fields`, `--raw`, `--log-level`, `--log-format`, `--debug`, `--profile`, `--non-interactive`.
- `--quiet` (`-q`): suppresses `rules`, `skills`, and `feedback` from response payload (saves tokens for experienced agents).
- `--raw`: outputs bare value lines to stdout for pipe chaining.
- `--fields`: comma-separated field projection to reduce response payload size.
- `--dry-run`: previews feedback mutations (`create`, `close`, `transition`) without modifying files.
- `--log-format=json`: emits single-line JSON log objects (JSONL) on stderr parseable by `jq`, `Vector` VRL, `Fluent Bit`, `Fluentd`.
- Inline context: every response includes `rules` (full `.md` content inline), `skills` (catalog), and `feedback` guidance.
- Built-in feedback system: `feedback create/list/show/close/transition`, stored in project source at `feedback/{id}.json`.
- Content commands use `--ai-summary/--no-ai-summary` (default disabled) for the detailed source-grounded Gemini summary; the former `--summary-mode` option is removed.

## Commands

```bash
uv run web-search-cli schema
uv run web-search-cli doctor
uv run web-search-cli getskill
uv run web-search-cli skills [name]
uv run web-search-cli feedback create --message "..." --type bug
uv run web-search-cli feedback list
uv run web-search-cli reference tools
uv run web-search-cli search quick --search-query ... --objective ...
uv run web-search-cli content <url>
uv run web-search-cli links <url>
uv run web-search-cli ai <query>
uv run web-search-cli youtube search <query>
uv run web-search-cli youtube transcript <video-id>
uv run web-search-cli analytics query
uv run web-search-cli analytics report <name>
uv run web-search-cli experiments list|create|enable|disable|conclude|stats
uv run web-search-cli sitemap generate <url>
```

## Testing

```bash
uv run pytest tests/cli/
```
