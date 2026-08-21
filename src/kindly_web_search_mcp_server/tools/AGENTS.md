# AGENTS.md - Tools

MCP tool metadata, profiles, catalog, and visibility helpers.

## Key Files

| File | Role |
|---|---|
| `catalog.py` | Tool catalog with metadata (profile, tags, timeouts, annotations) |
| `profiles.py` | Tool profile application (`regular` / `full`), visibility gating |
| `search.py` | `web_search` MCP tool implementation |
| `content.py` | `get_content`, `batch_get_content`, `discover_links` MCP tools |
| `academic.py` | `academic_search` MCP tool |
| `code_search/` | Agent-oriented public-code search with automatic backend channel selection |
| `code_search/filters.py` | Provider-neutral validation of repository, path, filename, extension, and language scopes |
| `ai_search.py` | `gemini_search`, `grok_search` |
| `youtube.py` | `youtube_search`, `youtube_transcript` |
| `sitemap.py` | `generate_sitemap` |
| `recommend.py` | `recommend_command` route recommendation tool |
| `prompts.py` | Prompt function implementations |
| `resources.py` | Resource implementations (8 resources) |
| `_helpers.py` | Lifespan management, domain filters, timeout resolution |

## Key Files (Metadata Layer)

| File | Role |
|---|---|
| `catalog.py` | `TOOL_CATALOG` with per-tool metadata |
| `profiles.py` | Profile-based tool visibility filtering |

## Tool Contracts

| Tool | Returns | Notes |
|---|---|---|
| `recommend_command` | Structured route, command, fallbacks, decomposition rules, and optional prompt metadata | Deterministic recommendation only; never executes commands |
| `web_search` | Title, link, snippet (no page content) | Lightweight search results |
| `get_content` | LLM-ready markdown for single URL | Content extraction |
| `gemini_search` | Grounded answers with citations | Uses Gemini + Google Search |
| `youtube_transcript` | Video transcripts | Optional translation/formatting |
| `youtube_search` | YouTube video results | YouTube Data API v3 or SearXNG |
| `generate_sitemap` | Structured site URL map | Tavily Map only |
| `code_search` | Typed code/documentation hits, repository candidates, and diagnostics | Backend selects lexical, symbol, regex, semantic, repository, and documentation channels; bounded cloud cross-encoder reranking is always attempted fail-open |

`get_content` and `batch_get_content` accept `ai_summary: bool = false`; when enabled they return only the detailed source-grounded Gemini summary. The former `summary_mode` and brief-summary option are removed.

## Rules

- Actual MCP tool implementations live in this directory + feature packages.
- Visibility is profile-based via `profiles.py`, not hard-coded in call sites.
- Tool orchestration belongs in tool functions, not service adapters.
- `emit_tool_observability_event` assigns one stable `tool_call_id` per invocation and writes bounded typed lifecycle rows to analytics `tool_calls`; request/response/error events must reuse that ID.
- Tool telemetry payloads exclude credential-like fields and classify response rows as `success`, `empty`, `partial`, or `error` from explicit status/error/result counts.
- `code_search/` keeps its typed `CodeSearchHit`/`CodeSearchResultType` boundary separate from `WebSearchResult`; provider adapters must not mutate the existing search providers.
- `code_search` supports explicit modes: `code`, `docs`, `discovery`, and exclusive `huggingface` semantic Hub asset search. Hugging Face mode uses the public librarian-bots API, preserves asset metadata and semantic-score semantics, and does not run GitHub/code providers.
- Natural, concept-heavy code queries are enriched privately by the existing GLiNER2 `/classify` + `/ner` service and `worker_llm` chain. An optional `research_goal` is passed separately to query rewriting and reranking, never compiled into provider syntax; exact identifiers, regexes, and repository-scoped queries skip LLM rewriting; all model output is validated as engine-neutral terms before deterministic provider compilation.
- Sourcegraph receives native `content:`, `sym:`, `repo:`, `file:`, and `lang:` syntax. GitHub, grep.app, and Exa must enforce the same explicit repository/path/language scopes; `filters.py` applies the provider-neutral post-filter before ranking.
- grep.app uses its stateless JSON-RPC `tools/call` SSE contract directly, with the literal `searchGitHub` arguments and bounded retry behavior used by established grep.app clients; REST remains a diagnostic fallback.
- Results retain provider/query provenance, match spans, symbols, exact hydrated revisions, compact source windows, evidence roles, and repository proof paths so agents can continue investigating.
- Tree-sitter is the canonical source classifier for complete hydrated files in Python, JavaScript/TypeScript, Go, Rust, Bash/shell, Java, HTML, and SQL. AST evidence is stored in private `source_metadata`; snippet-only or uncached-grammar results remain explicit unknown/native evidence.
- Tree-sitter grammar assets are never downloaded on the search hot path. Prefetch the approved set during deployment with `uv run python scripts/prefetch_tree_sitter.py` (or the environment's direct Python executable); missing cached grammars fail open.
- Hosted GLiNER2 package/repository entities are confidence-gated hints only. Context7/DeepWiki resolution verifies the hint; unresolved entities never invent repository URLs.
- Every `CodeSearchHit` exposes `result_kind` and `location` precision/availability metadata; adapters preserve branch refs separately from immutable revisions, never infer line coordinates from semantic highlights, and Exa Context remains aggregated semantic evidence without line precision.
- Aggregate outcome semantics treat `no_hit` and `skipped` diagnostics as clean absence; only `partial` and `error` diagnostics downgrade the public result state.
- Provider-specific library identifiers may remain in `source_metadata`, while top-level repository fields use canonical `owner/repo` formatting.
- GitHub uses only legacy REST code-search operators actually supported by `/search/code`; repository discovery uses GraphQL repository search and returns topics, SPDX license, homepage, default-branch name, and head OID. Preserve the distinct blob SHA and indexed commit OID and verify them during hydration.
- Cloud reranking is always-on and fail-open: `code_search` sends the bounded selected candidate pool through the existing cloud cross-encoder fallback chain when configured, using private `code`, `documentation`, or `hybrid` instructions selected from the internal query plan; general `web_search` reranking is unchanged. Local `sentence-transformers` models are not used, and failures preserve deterministic retrieval order. The former `rerank` parameter has been removed.

## Testing

```bash
uv run pytest tests/test_tool_descriptions.py tests/test_server.py
uv run pytest tests/test_tool_profiles.py
```

Focused code-search coverage:

```bash
uv run pytest tests/test_code_search.py
```

## Recent Changes (2026-07-22 sprint 2)
- `content.py` — removed orphan imports `from ..models import PageMetadata` (class deleted from `models.py`) and `from ..utils.stopwatch import Stopwatch` (module + class deleted). The 3 `timer = Stopwatch()` declarations + 6 `timer.elapsed_ms()` callsites replaced with `duration_ms=0` since `record_mcp_tool_call` requires the kwarg. No measurement infrastructure exists; restore Stopwatch + start/stop instrumentation in a future sprint if `record_mcp_tool_call` duration telemetry is needed.

- `code_search/` — Exa now uses the documented Context endpoint; provider-neutral scope filtering, positive one-based location normalization, result-kind-aware ranking, and transient-failure partial outcomes protect the typed search contract. Context request IDs, echoed queries, usage/cost metadata, and documented error tags/status classes remain available for diagnosis; the synthesized response is one bounded semantic hit without fabricated line precision.

## Grok Search Contract

- `ai_search.py::grok_search` uses the direct xAI Responses API and native `web_search` + `x_search`; responses include backend, tool-call, source, cache-token, and reasoning-token diagnostics.
- Keep the user-facing MCP signature stable. `model` is an xAI model ID (for example `grok-4.5`), not an OpenRouter-prefixed ID.
- The tool reports a configuration error when `GROK_BACKEND=vertex`, because Vertex's managed Grok Responses endpoint does not currently provide native xAI web/X search.
- Treat Grok as an expensive tool: xAI bills server-side search invocations separately from model input/output tokens. Do not hide those counts from telemetry or responses.
