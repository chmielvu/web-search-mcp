<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-17 | Last verified: 2026-09-17 -->

# AGENTS.md - Tools

MCP tool metadata, profiles, catalog, and visibility helpers.

## Key Files

| File | Role |
|---|---|
| `catalog.py` | Tool catalog with metadata (profile, tags, timeouts, annotations) |
| `profiles.py` | Tool profile application (`regular`, `research`, `media`, `full`), visibility gating |
| `content.py` | `fetch` MCP tool |
| `academic.py` | `academic_search` MCP tool |
| `ai_search.py` | `gemini_search`, `grok_search` |
| `youtube.py` | `youtube_transcript` (video URL/ID **or** channel handle/ID/URL, auto-detected; channel mode adds `max_videos`/`page_token`). Video discovery lives in `quick_web_search` mode='youtube' |
| `sitemap.py` | `generate_sitemap` |
| `prompts.py` | Prompt function implementations |
| `resources.py` | Resource implementations (8 resources) |
| `status.py` | Server status and provider health tools |
| `workflow.py` | Multi-tool workflow helpers |
| `_helpers.py` | Lifespan management, domain filters, timeout resolution |
| `deep_research.py` | `deep_research` MCP tool and background-task registration |

## Key Files (Metadata Layer)

| File | Role |
|---|---|
| `catalog.py` | `TOOL_CATALOG` with per-tool metadata |
| `profiles.py` | Profile-based tool visibility filtering |

## Tool Contracts

| Tool | Returns | Notes |
|---|---|---|
| `web_search` | Ranked multi-engine web hits with bounded adaptive retrieval | No public `status`; empty `results` vs `warnings`; `cursor` pages leftover title/url links (no synthesis, no new searching); `next` is fetch of ≤5 URLs; fresh searches add `synthesis` (snippet-grounded, `[cN]`) and `search` execution metadata (`rounds`, `stop_reason`). Catalog v4.0, no tool-wide timeout (three-wave runs must not be killed by one). Deprecated `gl` aliases to `region`. |
| `fetch` | LLM-ready Markdown or typed content for one or many URLs |
| `gemini_search` | Grounded answers with citations | Uses Gemini + Google Search |
| `youtube_transcript` | Video or channel transcripts | Auto-detects video vs channel target; channel mode reports per-video partial failures (`max_videos`, `page_token`); the former `youtube_channel_transcription` tool is merged into it |
| `generate_sitemap` | Structured site URL map | Tavily Map only |

- `fetch` accepts `ai_summary: bool = false`; when enabled the synthesized answer replaces public `content`, while the full summary object remains internal for analytics.

## Rules

- Actual MCP tool implementations live in this directory + feature packages.
- Visibility is profile-based via `profiles.py`, not hard-coded in call sites.
- Tool orchestration belongs in tool functions, not service adapters.
- `emit_tool_observability_event` assigns one stable `tool_call_id` per invocation and writes bounded typed lifecycle rows to analytics `tool_calls`; request/response/error events must reuse that ID.
- Tool telemetry payloads exclude credential-like fields and classify response rows as `success`, `empty`, `partial`, or `error` from explicit status/error/result counts.

## Testing

```bash
uv run pytest tests/test_tool_descriptions.py tests/test_server.py
uv run pytest tests/test_tool_profiles.py
```

## Grok Search Contract

- `ai_search.py::grok_search` uses the direct xAI Responses API and native `web_search` + `x_search`; responses include backend, tool-call, source, cache-token, and reasoning-token diagnostics.
- Keep the user-facing MCP signature stable. `model` is an xAI model ID (for example `grok-4.5`), not an OpenRouter-prefixed ID.
- The tool reports a configuration error when `GROK_BACKEND=vertex`, because Vertex's managed Grok Responses endpoint does not currently provide native xAI web/X search.
- Treat Grok as an expensive tool: xAI bills server-side search invocations separately from model input/output tokens. Do not hide those counts from telemetry or responses.
### Recent Changes (2026-09-06)
- `fetch` now returns a compact public `FetchResult` with typed errors and
  status-based access-wall outcomes. Internal artifact, cache, summary, and
  analytics fields remain private to the tool pipeline.
- `fetch` single-item responses populate `mode="single"` and summary failures
  no longer emit success telemetry while silently returning raw content.

- `fetch` / `crawl_web` FastMCP catalog timeouts are 240s so Web Unlocker
  can finish after Jina/Crawl4AI/Camoufox. Timeout errors are retryable and
  name the actual budget.

### Recent Changes (2026-09-18, adaptive web_search)
- `web_search` is the bounded adaptive operation: broad first fanout unchanged, LLM-proposed targeted second wave, LLM finish/third-wave decision, globally ranked union plus one `[cN]`-validated snippet-grounded synthesis. Catalog version 4.0; `_TOOL_TIMEOUTS["web_search"]` is `None` so a three-wave run is not killed by the one-pass tool-wide timeout (provider/reranker/LLM call timeouts and caller cancellation stay operative). `rewrite` only disables the first wave's planner rewrite; adaptive waves always run.

### Recent Changes (2026-09-18)
- `deep_research` lives in this package and registers through `TaskConfig(mode="optional")` plus FastMCP 4 `Progress`. Legacy-era clients still run it synchronously.
- `fetch` public results omit rumdl lint, empty optionals, complete-body
  `window`, and envelope telemetry (`total_chars_returned`, `wave_size`,
  `waves_completed`, `duration_ms`). Recovery diagnostics stay:
  `summary_failed`, `jina_warning`, `index_output_failed`.

### Recent Changes (2026-09-12)
- `youtube_transcript` projects summary payloads to semantic fields only;
  provider, model, and token-usage metadata stay internal.
