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
| `ai_search.py` | `gemini_search`, `grok_search` |
| `youtube.py` | `youtube_search`, `youtube_transcript` |
| `sitemap.py` | `generate_sitemap` |
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
| `web_search` | Title, link, snippet (no page content) | Lightweight search results |
| `get_content` | LLM-ready markdown for single URL | Content extraction |
| `gemini_search` | Grounded answers with citations | Uses Gemini + Google Search |
| `youtube_transcript` | Video transcripts | Optional translation/formatting |
| `youtube_search` | YouTube video results | YouTube Data API v3 or SearXNG |
| `generate_sitemap` | Structured site URL map | Tavily Map only |

`get_content` and `batch_get_content` accept `ai_summary: bool = false`; when enabled they return only the detailed source-grounded Gemini summary. The former `summary_mode` and brief-summary option are removed.

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

## Recent Changes (2026-07-22 sprint 2)
- `content.py` — removed orphan imports `from ..models import PageMetadata` (class deleted from `models.py`) and `from ..utils.stopwatch import Stopwatch` (module + class deleted). The 3 `timer = Stopwatch()` declarations + 6 `timer.elapsed_ms()` callsites replaced with `duration_ms=0` since `record_mcp_tool_call` requires the kwarg. No measurement infrastructure exists; restore Stopwatch + start/stop instrumentation in a future sprint if `record_mcp_tool_call` duration telemetry is needed.

## Grok Search Contract

- `ai_search.py::grok_search` uses the direct xAI Responses API and native `web_search` + `x_search`; responses include backend, tool-call, source, cache-token, and reasoning-token diagnostics.
- Keep the user-facing MCP signature stable. `model` is an xAI model ID (for example `grok-4.5`), not an OpenRouter-prefixed ID.
- The tool reports a configuration error when `GROK_BACKEND=vertex`, because Vertex's managed Grok Responses endpoint does not currently provide native xAI web/X search.
- Treat Grok as an expensive tool: xAI bills server-side search invocations separately from model input/output tokens. Do not hide those counts from telemetry or responses.
