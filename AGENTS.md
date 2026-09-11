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

This project is indexed by GitNexus as **web-search-mcp** (13489 symbols, 22293 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).


<!-- gitnexus:end -->
