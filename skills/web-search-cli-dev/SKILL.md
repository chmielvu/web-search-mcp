---
name: web-search-cli-dev
description: Developer skill for the web-search-cli package — repo map, contributor pointers, and CLI extension contract.
---

# web-search-cli-dev

Contributor skill for the Typer CLI package at `src/kindly_web_search_mcp_server/cli/`. Use this when you are adding or modifying a CLI command, wiring a new MCP tool into the surface, editing the JSON envelope, or updating the agent context (`agent/`, `skills/`) shipped with the package.

## When to use

Use this skill when you are editing CLI source — adding a command, refactoring the envelope, touching the skill / rule / catalog wiring, or wiring a new MCP tool into the CLI surface. For pure user-facing invocations, use the `web-search-cli` skill instead.

## Repo map

```
src/kindly_web_search_mcp_server/cli/
├── app.py                   # Typer app + global options callback; registers every group
├── commands/                # One module per command group (Typer sub-app)
│   ├── schema.py            # `schema` — emits planned command tree
│   ├── doctor.py            # `doctor` — readiness checks, no provider calls
│   ├── getskill.py          # `getskill` — prints a bundled skill markdown file
│   ├── skills.py            # `skills` — list catalog or print named skill markdown
│   ├── feedback.py          # `feedback` — file/queue under feedback/{id}.json
│   ├── reference.py         # `reference tools|external-tools`
│   ├── search.py            # `search web|quick|academic|inspect|postmortem`
│   ├── content.py           # `content fetch|crawl`
│   ├── links.py             # `links discover|similar`
│   ├── ai.py                # `ai gemini|grok`
│   ├── youtube.py           # `youtube transcript|channel`
│   ├── analytics.py         # `analytics query|report`
│   ├── server.py            # `server start`
│   ├── sitemap.py           # `sitemap generate`
│   ├── jobs.py              # `jobs list|get|wait|cancel|resume`
│   ├── results.py           # `results search` over persisted payloads
│   ├── inference.py         # `inference describe|validate|chain`
│   └── research.py          # `research deep|collect`
├── services/                # CLI adapters that call into MCP tool implementations
│   ├── search_web.py
│   ├── quick_search.py
│   ├── content.py
│   ├── crawl.py             # wraps tools.content.crawl_web
│   ├── link_tools.py
│   ├── ai.py
│   ├── academic.py
│   ├── youtube.py
│   ├── sitemap.py
│   ├── jobs.py              # local durable job store
│   ├── results.py           # persist + query layer (duckdb_data/cli/)
│   ├── deep_research.py
│   └── files.py             # atomic write helpers
├── metadata.py              # cli_brief, rules_catalog, rules_full, skill_catalog, feedback_guidance, build_help_payload
├── skill_paths.py           # USER_SKILL_PATH / DEV_SKILL_PATH / AGENT_DIR resolution
├── output.py                # emit_json envelope + _suggested_next + --raw / --fields projection
├── runtime.py               # CliRuntime (--quiet/--raw/--fields/--human/--agent/--dry-run/...)
├── errors.py                # CliError + HintRule patterns → auth_error, not_found, rate_limited, …
├── job_worker.py            # background drain + write executor shutdown
├── outcome.py               # payload → CliError lifters (raise_for_payload_error, …)
├── introspection.py         # Typer → JSON schema reflection (build_schema_payload)
├── exit_codes.py            # ExitCode enum
├── bootstrap.py             # telemetry / logging init
└── AGENTS.md                # package-level contributor notes

src/kindly_web_search_mcp_server/tools/
└── catalog.py               # MCP tool catalog (timeouts, descriptions, schemas)
```

Top-level agent + skill surface (consumed by the CLI):

```
agent/
├── brief.md                 # one-paragraph project summary → cli_brief()
├── rules/
│   ├── trigger.md           # when to use the CLI vs MCP server
│   ├── workflow.md          # auth env vars, envelope, flags, discovery
│   └── writeback.md         # feedback create / close / transition loop
└── skills/                  # extra agent-private skills (auto-listed by skill_catalog)

skills/
├── web-search-cli/SKILL.md       # user skill → `web-search-cli getskill`
└── web-search-cli-dev/SKILL.md   # this file → `web-search-cli getskill --dev`
```

## CLI extension contract

When you add a new command or wire a new MCP tool:

1. **Define the command** in `cli/commands/<group>.py` as a Typer sub-app using `Annotated[..., typer.Option(...)]` annotations and a Google-style docstring. Raise `CliError(kind=..., message=..., hint=..., exit_code=ExitCode.X, context={...})` on every failure path; never `print` to stdout / stderr outside `emit_json` / `emit_error`.
2. **Wire the adapter** in `cli/services/<tool>.py` — keep CLI option names flat (nested tool models become either flattened flags or stay hidden with a brief justification comment).
3. **Map to the MCP tool** by adding a `ToolCoverageEntry` to `TOOL_COVERAGE` in `cli/reference_data.py` and the command path string to `COMMANDS`. Keep `tool`, `command`, `profiles`, and `required` aligned with `tools/catalog.py` so `reference tools` stays truthful.
4. **Register the group** in `cli/app.py` (import + `group.register(app)`); the order in `global_options` callback body matches help / schema output.
5. **JSON envelope** — return a plain dict from your service, let `emit_json(payload, command="group subcommand")` and `output.py::_suggested_next` build the envelope. Don't hand-roll `schema_version` / `meta` blocks.
6. **Persistence** — `emit_json` auto-persists non-help payloads via `services.results.persist_cli_result`. Skip persistence only by raising `CliError` or by returning from `command != "results search" and not command.endswith(" --help")` (already handled).
7. **Skill / rule updates** — if you add a new auth env var, surface it in `agent/rules/workflow.md`. If you add a new top-level group, document it in `skills/web-search-cli/SKILL.md` and here.

## Discovery + help pipeline

`app.main` handles `--brief` (uses `cli_brief`), `--version`, and `--help` specially:

- `--brief` → `agent/brief.md` if present, else first paragraph of `skills/web-search-cli/SKILL.md`.
- `--version` → `importlib.metadata.version("web-search-mcp")` with `pyproject.toml` fallback.
- `--help` → `build_full_help_payload(app, args)` which composes `build_help_payload` (command node + skills) with `rules_catalog()` and `feedback_guidance()`.

`build_help_payload` walks `command_path_tokens(args)` to find the right Typer node via `introspection.find_command_node`. `_global_option_tokens` is the source of truth for which flags are stripped before path matching — keep new global flags there.

## Output envelope

`emit_json` (and `emit_error`) own:

- `schema_version = "1.0"` (constant in `output.py`).
- `meta` — `command`, `profile`, `quiet`, `log_level`, `log_format`, `debug`, `non_interactive`, `raw`, `fields`, `dry_run`, `duration_ms`, `generated_at`, optional `run_key`.
- `data` — your payload, optionally projected by `runtime.fields`.
- `suggested_next` — derived from `data["window"]["has_more"]`, `data["has_more"]` + `data["cursor"]`, and `data["next"]` continuation hints (extend `_suggested_next` if you add a new pattern).
- `rules`, `skills`, `feedback` — auto-injected unless `runtime.quiet`.

`--raw` skips the envelope entirely and writes the projected data as bare value lines; respect it by returning a flat list / dict of strings whenever you have a choice.

## Errors

Every failure must raise `CliError` (`cli/errors.py`) with `kind`, `message`, `hint`, `exit_code`, and `context`. Generic exceptions hit `match_hint_rule` (regex over the message: `401|unauthorized|invalid token|api key` → `auth_error`, `404|not found|unknown command` → `not_found`, `rate limit|429` → `rate_limited`, `network|timeout|dns` → `network_error`, …). Add new patterns to `HINT_RULES` when a new error class deserves a tailored hint.

## Adding agent / skill surface

- New rule → drop a `*.md` into `agent/rules/`. Frontmatter is optional; if you add `---` fences, the `description:` value is what `rules_catalog()` exposes (single line; surrounding quotes are stripped).
- New user skill → either a sibling `skills/<name>/SKILL.md` (catalogued as `<name>`) or a single `agent/skills/<name>.md` (catalogued as `<name>`). The user-facing skill (`web-search-cli`) ships from `skills/`, not `agent/skills/`.
- New developer skill → add to `skills/web-search-cli-dev/` is reserved for this file. New developer-flavored material belongs in `agent/skills/`.

Verify locally without running tests: `uv run python -c "from kindly_web_search_mcp_server.cli.metadata import cli_brief, rules_catalog, skill_catalog, feedback_guidance; print(cli_brief()); print([r['name'] for r in rules_catalog()]); print([s['name'] for s in skill_catalog()])"`.
