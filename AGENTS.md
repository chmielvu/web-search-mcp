<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md — Kindly Web Search MCP Server

Experimental FastMCP server + Typer CLI for an experimental multi-provider web search, content extraction, reranking, analytics, and AI-grounded answers.

### Code Organization
- **Requirement**: Restructure code into logical modules and packages
- **Acceptance Criteria**:
  - Clear separation of concerns
  - Proper package structure with `__init__.py` files
  - Logical grouping of related functionality
  - Elimination of circular dependencies

### Python Best Practices
- **Requirement**: Implement Python coding standards throughout
- **Acceptance Criteria**:
  - PEP 8 compliance for code style
  - PEP 484 type hints where appropriate
  - Proper use of Python idioms and patterns
  - Consistent error handling with appropriate exception types
  - Context managers for resource management

### Library Standardization
- **Requirement**: Replace custom implementations with standard libraries
- **Acceptance Criteria**:
  - Use of established libraries for common tasks
  - Proper dependency management
  - Removal of redundant or outdated dependencies
  - Consistent library usage patterns

###  Code Quality
- **Requirement**: Improve overall code quality and maintainability
- **Acceptance Criteria**:
  - Elimination of code duplication
  - Proper abstraction and encapsulation
  - Clear and consistent naming conventions
  - Comprehensive docstrings for modules, classes, and functions
  - Removal of dead code and unused imports

###  Maintainability
- Code should be easily understandable by Python developers
- Clear documentation of complex logic
- Modular design allowing for future extensions

### Development Experience
- Consistent development patterns throughout the codebase
- Clear import structure
- Intuitive file and module organization

## Comments

Every code comment must stand alone for a reader without access to the authoring context.

- Describe current behavior and invariants; avoid temporal wording such as “new,” “old,” “temporary,” or “recently.”
- Do not refer to conversations, ephemeral materials, authors, branches, tickets, or local-only paths.
- Cite external specifications with durable URLs when a citation is necessary.
- Do not describe work as merged, landed, or shipped; repository history records that context.


## Commands (verified)
> **Experimental app / testing freeze:** This is an experimental application. Tests are currently frozen and intentionally ignored. Agents **MUST NOT run any test command, test file, test discovery, or test suite**, and **MUST NOT add, modify, repair, or unfreeze tests** unless explicitly instructed.

| Task | Command | ~Time |
|------|---------|-------|
| Install | `uv sync` | ~5s |
| Lint | `uv run ruff check src/` | <1s |
| Format Check | `uv run ruff format --check src/` | <1s |
| Type Check | `uv run ty check src` | ~2s |
| Run MCP Server | `uv run web-search-cli server` | foreground |
| CLI Doctor | `uv run web-search-cli doctor` | ~8s |

## CLI & MCP Tools Overview


### Typer CLI (`uv run web-search-cli <command>`)
- **Core Commands**: `doctor`, `schema`, `reference tools`, `skills`, `getskill`, `feedback`
- **Search & Fetch Operations**: `search web`, `search quick`, `search academic`, `search code`, `content <url>`, `links <url>`, `ai <query>`, `youtube search/transcript`, `sitemap generate`
- **Analytics & Labs**: `analytics query`, `analytics report <name>`, `experiments list|create|enable`
- **Global Flags**: `--brief`, `--quiet` (`-q`, suppresses rules/skills), `--raw` (bare stdout), `--fields` (field projection), `--log-format=json`, `--dry-run`

## Analytics & DuckDB Databases

Persistent `.duckdb` databases use DuckDB's native single-writer format. External process queries or DuckDB CLI invocations **MUST** run in `READ_ONLY` mode (e.g., `duckdb "duckdb_data/analytics/search_events.duckdb?read_only=true"` or `uv run web-search-cli analytics query`).

earch_branches`, `provider_calls`, `final_results`, `llm_call_log`, `llm_judgments`, `vw_events`, `vw_quality_events`, `vw_run_timeline`, `vw_provider_results`, `vw_cache_lookups`.

## Package Guides (Progressive Disclosure)

| Navigate to | When modifying |
|---|---|
| `src/kindly_web_search_mcp_server/search/AGENTS.md` | Search pipeline (planning, retrieval, ranking, web search providers) |
| `src/kindly_web_search_mcp_server/content/AGENTS.md` | Content fetching pipeline (resolvers, extraction stages) |
| `src/kindly_web_search_mcp_server/rerank/AGENTS.md` | Multi-stage reranking (BM25, bi-encoder, cross-encoder, RankLLM) |
| `src/kindly_web_search_mcp_server/analytics/AGENTS.md` | DuckDB analytics, quality metrics, LLM judge pipeline |
| `src/kindly_web_search_mcp_server/cli/AGENTS.md` | CLI commands, services, reserved flags |
| `src/kindly_web_search_mcp_server/tools/AGENTS.md` | MCP tool metadata, profiles, catalog |
| `src/kindly_web_search_mcp_server/cache/AGENTS.md` | In-memory LRU + DuckDB page/transcript caches |
| `src/kindly_web_search_mcp_server/youtube/AGENTS.md` | Transcript cascade, cobalt/Cloudflare/Space ASR tiers, Data API v3 |
| `src/kindly_web_search_mcp_server/inference/AGENTS.md` | Model & provider registry, fallback engine, adapters |
| `src/kindly_web_search_mcp_server/middleware/AGENTS.md` | FastMCP middleware (rate limits, guidance, protection) |
| `src/kindly_web_search_mcp_server/prompts/AGENTS.md` | Prompt templates and registry |
| `src/kindly_web_search_mcp_server/ml/AGENTS.md` | ML gateway clients (fastembed embeddings, GLiNER2 entity extraction) |
| `src/kindly_web_search_mcp_server/ab_testing/AGENTS.md` | A/B testing framework |
| `src/kindly_web_search_mcp_server/evals/AGENTS.md` | Evaluation test cases, LLM judges, regression metrics |
| `src/kindly_web_search_mcp_server/observability/AGENTS.md` | Observability event helpers |
| `src/kindly_web_search_mcp_server/telemetry/AGENTS.md` | OpenTelemetry instrumentation |
| `src/kindly_web_search_mcp_server/training/AGENTS.md` | Write-only JSONL training data sink |
| `src/kindly_web_search_mcp_server/utils/AGENTS.md` | Cross-cutting helpers (HTTP, logging, async) |
| `tests/AGENTS.md` | Reference only; tests are frozen, ignored, and forbidden to run or modify unless explicitly instructed |
| `docs/AGENTS.md` | Human-readable documentation |
| `duckdb_data/AGENTS.md` | DuckDB database inventory and read-only access |
| `prototypes/public_code_search/AGENTS.md` | Agent-oriented public GitHub code search prototype |

## Boundaries

### Always Do
- Run `impact({target: "symbolName", direction: "upstream"})` before editing any symbol & report blast radius.
- Run `detect_changes()` before committing to verify affected execution flows.
- Use `READ_ONLY` mode when querying DuckDB database files outside the main server.
- Update nearest package `AGENTS.md` when changing a subsystem.
- Document changes in `CHANGELOG.md` under `[Unreleased]` and update `.agent/CONTINUITY.md`.

### Ask First
- Modifying `settings.py` (env var config affects all subsystems) or `pyproject.toml` (dependency changes).
- Changing public MCP tool contracts or CLI JSON output schemas.

### Never Do
- Modify files in `.agent/`, `.gitnexus/`, or `.venv*`.
- Commit API keys or credentials.
- Perform direct writes to `.duckdb` files from external CLI scripts while server runs.
- Blame external APIs for timeouts — root cause is always local code.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **web-search-mcp** (10603 symbols, 17956 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/web-search-mcp/context` | Codebase overview, check index freshness |
| `gitnexus://repo/web-search-mcp/clusters` | All functional areas |
| `gitnexus://repo/web-search-mcp/processes` | All execution flows |
| `gitnexus://repo/web-search-mcp/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## Python Coding Standards

### Tooling

Formatting and linting are handled by `ruff`; types by `ty` (Astral's Rust type checker, the same family as `ruff` and `uv`). Configure once, let the tools decide style debates.

The enforced configuration lives in `pyproject.toml`:

```toml
# pyproject.toml (enforced)
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "C4", "SIM", "RUF"]
ignore = [
    "E501",  # line length is the formatter's job
    "B008",  # FastMCP resolves parameters from their default annotations
]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

`I`, `UP`, `B`, `C4`, `SIM`, and `RUF` are enforced, so imports must be sorted, typing
imports must be modern (`collections.abc`, builtin generics, PEP 695 where the code already
uses it), and swallowed exceptions must say so (`contextlib.suppress`) rather than hiding in
`except: pass`. Per-file ignores exist for the two patterns that are deliberate here — the
typographic characters documented in `utils/text_clean.py`, and Typer's repeated-option default
in `cli/commands/search.py`.

The type-checker configuration:

```toml
[tool.ty.environment]
python = ".venv"
python-version = "3.12"
root = ["./src"]
```

Run before calling anything done:

```bash
uv run ruff check src/
uv run ruff format --check src/
uv run ty check src
```

Widening the ruff rule set further (`PERF`, `N`, `ANN`) is a deliberate, standalone change: it
needs a repo-wide fix pass, so it is not smuggled into unrelated edits.

### Type checking

`ty` is the type checker. A full-tree run costs ~2–4s, so there is no reason to skip it:

```bash
uv run ty check src                  # whole tree
uv run ty check src/path/to/file.py  # one file while iterating
```

> **Running the server from this venv?** An MCP client that has `web-search-mcp.exe` loaded locks the
> whole environment: a bare `uv run` or `uv sync` starts reinstalling, deletes the other console
> scripts (`web-search-cli.exe`, `mcp-server.exe`), then aborts on the locked file — leaving the CLI
> entry points missing until a sync succeeds. Always use `uv run --no-sync ty check src` while a
> client is attached, and re-run `uv sync` once it is stopped to restore the scripts.

Configuration is the `[tool.ty]` table in `pyproject.toml`:

- `[tool.ty.environment]` pins the interpreter to `.venv` and the target to Python 3.12.
- `[tool.ty.analysis].allowed-unresolved-imports` lists modules that are genuinely optional at runtime (`curl_cffi`, `fitz`, `gradio_client`, the OTEL Prometheus exporter). **Never add an entry there to silence a real resolution failure** — that rule is what exposed the stale `rerank.llm_rerank` import that had been silently disabling the LLM rerank stage.

Rules for agents:

- **Do not add new diagnostics.** When you touch a file, fix the `ty` errors it already has.
- Suppress narrowly at the site with `# ty: ignore[rule-name]` and a stated reason; never relax a rule globally to make a file pass. Note that mypy-style codes do not match ty's rule names — `# type: ignore[arg-type]` suppresses nothing here, because ty looks for `invalid-argument-type`. Prefer fixing the producer (type the helper parameter or local variable as the literal it feeds) over suppressing at the consumer.
- **The backlog is cleared:** as of 2026-09-16 `uv run ty check src` is clean, so the CI step is **blocking** (no `continue-on-error`). The remaining `# ty: ignore[...]` comments are all library-stub gaps (telethon, `ddgs`, google-genai, anyio, the OpenAI-compatible client shape) and each carries its reason inline.
- `[tool.pyright]` stays in `pyproject.toml` for editor integrations (Pylance); `ty` is what the command line and CI enforce.

### Naming

PEP 8, with clarity valued over brevity:

- **Files/modules**: descriptive `snake_case` (`entity_lookup.py`, not `ent_lk.py`)
- **Classes**: `PascalCase`; acronyms stay uppercase (`HTTPClient`, not `HttpClient`)
- **Functions/variables**: `snake_case`
- **Constants**: `SCREAMING_SNAKE_CASE` (`MAX_RETRY_ATTEMPTS = 3`)

### Imports

Group in order — standard library, third-party, local. Inside `kindly_web_search_mcp_server`, relative imports (`from .models import …`, `from ..utils.x import …`) are the convention, as used throughout `src/`:

```python
# Standard library
import os
from collections.abc import Callable
from typing import Any

# Third-party packages
import httpx
from pydantic import BaseModel

# Local imports (relative inside the package)
from .models import WebSearchResponse
from ..utils.url_canonicalize import canonicalize_url
```

### Type Annotations

All public APIs get type hints. Modern builtin generics only (`list[str]`, `X | None`), no `typing.List`/`Optional`.

### Docstrings

Google-style docstrings on all public classes, methods, and functions. One-liner for the simple stuff; full `Args` / `Returns` / `Raises` / `Example` sections when the signature actually warrants it. Docstrings live next to the code they describe and get updated with it, not left to rot.

```python
def get_entity(entity_id: str) -> Entity | None:
    """Retrieve an entity by its Wikidata ID.

    Args:
        entity_id: Wikidata entity identifier, e.g. "Q42".

    Returns:
        The matching entity, or None if it does not exist.

    Raises:
        APIError: If the Wikidata API request fails.
    """
```

### Formatting

- Line length 120 characters — break long signatures across lines for readability
- Double quotes, space indentation (enforced by `ruff format`)
