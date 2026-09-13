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
| Lint | `uv run ruff check src/ tests/` | <1s |
| Format Check | `uv run ruff format --check src/ tests/` | <1s |
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

This project is indexed by GitNexus as **web-search-mcp** (9505 symbols, 16373 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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

Formatting and linting are handled by `ruff`; types by `mypy`. Configure once, let the tools decide style debates.

```toml
# pyproject.toml
[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes
    "I",    # isort
    "B",    # flake8-bugbear
    "C4",   # flake8-comprehensions
    "UP",   # pyupgrade
    "SIM",  # flake8-simplify
]
ignore = ["E501"]  # line length is the formatter's job

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_ignores = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
```

Run before calling anything done:

```bash
ruff check --fix .
ruff format .
mypy .
```

### Naming

PEP 8, with clarity valued over brevity:

- **Files/modules**: descriptive `snake_case` (`entity_lookup.py`, not `ent_lk.py`)
- **Classes**: `PascalCase`; acronyms stay uppercase (`HTTPClient`, not `HttpClient`)
- **Functions/variables**: `snake_case`
- **Constants**: `SCREAMING_SNAKE_CASE` (`MAX_RETRY_ATTEMPTS = 3`)

### Imports

Group in order — standard library, third-party, local — and use absolute imports only:

```python
# Standard library
import os
from collections.abc import Callable
from typing import Any

# Third-party packages
import httpx
from pydantic import BaseModel

# Local imports
from wikidata_cli.models import Entity
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
