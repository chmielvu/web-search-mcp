# Agentic Web Research

`agentic_web_research` is the new LangChain/LangGraph ReAct entrypoint for multi-hop research.

## What it does

- Uses `Alibaba-NLP/Tongyi-DeepResearch-30B-A3B` through NanoGPT's OpenAI-compatible subscription endpoint.
- Lets the model choose tools step-by-step instead of routing through the legacy full `web_search` pipeline.
- Keeps the inner loop on direct search, fetch, rerank, and expansion tools.
- Builds an ephemeral NetworkX reasoning graph per run so the final response can summarize coverage, duplicate domains, and potential conflicts.

## Tool surface

The agent can use these tools:

- `composio_web_search`
- `search_tavily`
- `search_brave`
- `search_duckduckgo`
- `composio_similarlinks`
- `composio_image_search`
- `get_content`
- `batch_get_content`
- `discover_links`
- `academic_search`
- `rerank_candidates`

## Environment

Required:

- `NANOGPT_API_KEY`

Optional overrides:

- `KINDLY_AGENTIC_RESEARCH_MODEL`
- `KINDLY_AGENTIC_RESEARCH_FALLBACK_MODELS`
- `KINDLY_AGENTIC_RESEARCH_GEMINI_FALLBACK_MODEL`
- `KINDLY_AGENTIC_RESEARCH_HF_ROUTER_BASE_URL`
- `KINDLY_AGENTIC_RESEARCH_HF_FALLBACK_MODEL`
- `KINDLY_AGENTIC_RESEARCH_BASE_URL`
- `KINDLY_AGENTIC_RESEARCH_TEMPERATURE`
- `KINDLY_AGENTIC_RESEARCH_TIMEOUT_SECONDS`
- `KINDLY_AGENTIC_RESEARCH_MAX_RETRIES`
- `KINDLY_AGENTIC_RESEARCH_QUICK_RUN_LIMIT`
- `KINDLY_AGENTIC_RESEARCH_NORMAL_RUN_LIMIT`
- `KINDLY_AGENTIC_RESEARCH_DEEP_RUN_LIMIT`
- `KINDLY_AGENTIC_RESEARCH_QUICK_TIMEOUT_SECONDS`
- `KINDLY_AGENTIC_RESEARCH_NORMAL_TIMEOUT_SECONDS`
- `KINDLY_AGENTIC_RESEARCH_DEEP_TIMEOUT_SECONDS`

Default model fallback order is:

1. `Alibaba-NLP/Tongyi-DeepResearch-30B-A3B` through NanoGPT
2. `minimax/minimax-m3:thinking` through NanoGPT
3. `mistralai/mistral-small-4-119b-2603:thinking` through NanoGPT
4. `gemini-3.5-flash` through the Gemini API when `KINDLY_GEMINI_API_KEY` is set
5. `openai/gpt-oss-120b:novita` through the Hugging Face router when `HF_TOKEN` is set

## Depth modes

- `quick`: smaller tool budget, faster finish.
- `normal`: default research budget.
- `deep`: higher tool budget and longer timeout.

The depth setting only changes the agent's tool budget and timeout. It does not force arbitrary page-count limits.

## Observability (Hybrid: Grafana/DuckDB + Langfuse)
The agent emits to the existing MCP observability stack (OTel spans/metrics/logs to Grafana, structured events to local DuckDB `.kindly/analytics/search_events.duckdb` + MotherDuck sync, `tool.agentic_web_research.*` events, mcp tool counters, Layer 3 signals via `record_agentic_research`).

The canonical analytics shape is:
- `tool.agentic_web_research.request` and `tool.agentic_web_research.response` at the MCP boundary
- `agentic.research.completed` for the inner runner completion record
- `duration_seconds` and `sources_count` are mirrored into `duration_ms` and `output_count` for DuckDB/MotherDuck views

Additionally, rich ReAct-specific traces (generations, tool observations with I/O, costs, per-step latency, trajectory) are sent to **Langfuse** using the standard integration:

- Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` (or the KINDLY_ equivalents for convenience). If you already have `LANGFUSE_MCP_AUTH_HEADER` from the Langfuse MCP config, the runtime now decodes it and reuses the contained project keys automatically.
- The runner attaches a `CallbackHandler` to the `create_agent` ainvoke (plus metadata for depth/model/goal and Langfuse session/tags).
- Post-run scores are attached automatically (source coverage, uncertainty flags) using the knowledge graph + result.
- OTel spans (general + custom agentic attrs) can also flow to Langfuse OTLP when the keys are present (dual export alongside Grafana).

See `docs/OBSERVABILITY.md` for full Grafana + DuckDB + Langfuse hybrid setup and verification steps. Use the provided project keys for the target Langfuse workspace when testing the agentic path.

Example (env):
```powershell
$env:LANGFUSE_PUBLIC_KEY="pk-lf-..."
$env:LANGFUSE_SECRET_KEY="sk-lf-..."
$env:LANGFUSE_BASE_URL="https://cloud.langfuse.com"
```
Or, if you already have the MCP auth header:
```powershell
$env:LANGFUSE_MCP_AUTH_HEADER="Basic <base64(LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY)>"
```

In Langfuse you will see hierarchical traces for each research run, with the Tongyi model generations, the internal tool calls (composio, tavily, get_content, academic, rerank, ...), and the final synthesized answer + sources.

This gives both unified pipeline visibility (Grafana quality dashboards, DuckDB SQL reports on agent runs) and agent-specific debugging/evals (Langfuse).
