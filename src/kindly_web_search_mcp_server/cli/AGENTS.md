# AGENTS.md - CLI (web-search-cli)

This directory contains the native Typer CLI for the MCP server.

## Current Structure

cli/
|-- app.py                   # Main Typer application entrypoint
|-- commands/                # Command registration modules
|   |-- schema.py            # schema command
|   |-- doctor.py            # doctor command
|   |-- getskill.py          # getskill command
|   |-- reference.py         # reference commands
|   |-- search.py            # search-related CLI commands
|   |-- content.py           # content commands
|   |-- links.py             # link-discovery commands
|   |-- ai.py                # AI/search synthesis commands
|   |-- youtube.py           # YouTube commands
|   |-- analytics.py         # analytics query/report commands
|   |-- experiments.py       # A/B experiment management
|   |-- server.py            # server/launch helpers
|   └── sitemap.py           # sitemap command (Tavily Map + legacy fallback)
└── services/               # Shared service adapters
    |-- search_web.py        # Web search service adapter
    |-- quick_search.py      # Quick search adapter
    |-- content.py           # Content fetch service adapter
    |-- content_batch.py     # Batch content adapter
    |-- link_tools.py        # Link discovery adapter
    |-- ai.py                # AI answer adapters
    |-- academic.py          # Academic search adapter
|-- youtube.py           # YouTube adapter
    └── sitemap.py           # Sitemap adapter

## Current Behavior

- `app.py` wires all commands into a JSON-first CLI.
- Global runtime flags include `--agent`, `--human`, `--quiet`,
  `--profile`, `--log-level`, `--debug`, and `--non-interactive`.
- `--debug` sets the CLI application log level to `DEBUG` and emits logs on
  stderr; structured command output remains on stdout.
- The CLI is the first-class surface; there is no `mcp2cli` compatibility
  wrapper.

## Main Commands

- `web-search-cli schema`
- `web-search-cli doctor`
- `web-search-cli getskill`
- `web-search-cli reference tools`
- `web-search-cli search ...`
- `web-search-cli content ...`
- `web-search-cli links ...`
- `web-search-cli ai ...`
- `web-search-cli youtube ...`
- `web-search-cli analytics query`
- `web-search-cli analytics report`
- `web-search-cli experiments list|enable|disable|conclude|stats|create`
- `web-search-cli sitemap generate`

## Testing

- `python -m pytest tests/cli/test_*.py`
- `python -m pytest tests/test_uvx_cli.py`
