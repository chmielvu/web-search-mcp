# Agentic Web Research — Code Review & Live Exercise Notes

**Module:** `src/kindly_web_search_mcp_server/agent/`
**Date:** 2026-06-03
**Reviewer:** opencode / ollama-cloud/minimax-m3

## What it is

A self-contained LangChain ReAct agent that exposes a single MCP tool —
`agentic_web_research` — for the model to drive its own search loop, instead of
being fed a pre-merged `web_search` result set.

### File map

| File | Role |
|---|---|
| `mcp.py` | FastMCP tool registration + OTel/DuckDB instrumentation |
| `runner.py` | The ReAct loop: model wiring, tool budget, result extraction |
| `model.py` | ChatOpenAI on `nano-gpt.com` with optional Gemini terminal fallback |
| `config.py` | Env-driven config; depth profiles (quick/normal/deep) |
| `prompts.py` | System prompt with budget + tool-priority rules |
| `toolset.py` | Aggregates tool groups + unconditional `final_answer` tool |
| `search_tools.py` | 6 search tools: composio (Exa), Tavily, Brave, DDG, similarlinks, image |
| `content_tools.py` | `get_content`, `batch_get_content`, `discover_links` (reuses fetch pipeline) |
| `rerank_tools.py` | `rerank_candidates` over candidate pools |
| `academic_tools.py` | `academic_search` (Semantic Scholar + ArXiv + OpenAlex + PubMed) |
| `knowledge_graph.py` | NetworkX-based source/tool graph + uncertainty detection |
| `models.py` | Pydantic contracts: request, result, tool inputs, `FinalAnswerInput` |

### Defaults (`settings.py:283-345`)

- Primary model: `Alibaba-NLP/Tongyi-DeepResearch-30B-A3B` via `nano-gpt.com`
- Fallback chain: `minimax/minimax-m3:thinking` →
  `mistralai/mistral-small-4-119b-2603:thinking` → Gemini 3.5 flash (terminal)
- Depth budgets: quick 6 calls / 120s, normal 10 / 180s, deep 16 / 300s

## How the loop runs (`runner.py`)

1. Build model with primary + 2-3 fallbacks (`model.py:48-57`).
2. Build system prompt with budget reminder (`prompts.py`).
3. Best-effort merge in **external MCP tools** if
   `AGENTIC_RESEARCH_EXTERNAL_MCP_CONFIG` is set (`runner.py:81-108`).
4. `create_agent(model, tools, system_prompt, middleware=[ToolCallLimitMiddleware(run_limit=..., exit_behavior="continue")])`
   (`runner.py:110-116`).
5. Best-effort Langfuse `CallbackHandler` for ReAct-specific tracing
   (`runner.py:128-156`).
6. `await asyncio.wait_for(agent.ainvoke(...), timeout=profile.timeout_seconds)`
   (`runner.py:159-165`).
7. Walk messages, ingest every ToolMessage payload into the
   `ResearchKnowledgeGraph` (`knowledge_graph.py:152-163`).
8. Detect explicit `final_answer` ToolMessage; prefer its payload, else fall
   back to last `AIMessage.content` text extraction (`runner.py:174-217`).
9. Emit DuckDB + OTel events; record metrics; Langfuse post-score.

## Cross-reference

| | **Tongyi reference** (the primary model was trained for this) | **This module** |
|---|---|---|
| Orchestrator | Custom text-prompt loop, parses `<tool_call>`/`</tool_call>` and `<answer>` | LangChain `create_agent` (built on LangGraph prebuilt) |
| Tool budget | `MAX_LLM_CALL_PER_RUN` (default 100), `while num_llm_calls_available > 0` | `ToolCallLimitMiddleware(run_limit=...)` |
| Hard time limit | 150 min wall-clock inside the loop | `asyncio.wait_for(timeout=...)` outer |
| Final answer | `<answer>...</answer>` text in assistant turn | Explicit `final_answer` Pydantic tool call |
| Tools | `tool_file`, `tool_scholar`, `tool_python`, `tool_search`, `tool_visit` (qwen_agent classes) | 6 search + 3 content + 1 rerank + 1 academic + final_answer |
| Token overflow | 110K context → force final answer | Handled by LangChain message trimming |
| Observability | stdout prints + result dict | DuckDB + OTel + Langfuse + Grafana |

The single biggest philosophical difference: **Tongyi extracts answers from
free-form text markers in the model output**; **this module makes the answer a
structured tool call**. The latter gives you (a) a Pydantic-validated payload,
(b) a single place to record `confidence` and `gaps`, and (c) no
regex-against-`<answer>` parsing. The tradeoff is that the agent must *want* to
call `final_answer` — when it doesn't, you fall back to the last
`AIMessage.content`, which is the source of the title/URL extraction bug
observed in the live exercise.

## Live-tool exercise (round 1 — 8 calls)

| # | Query | Depth | Outcome | Duration | final_answer used? |
|---|---|---|---|---|---|
| 1 | LangGraph ReAct best practice (initial) | quick | timeout at 120s | >120s | n/a |
| 2 | Latest Python 3.13 release | quick | success, 8 sources | 33.7s | yes |
| 3 | LangChain create_agent vs manual ReAct | normal | timeout at 180s | >180s | n/a |
| 4 | LangGraph create_agent middleware | normal | timeout at 180s | >180s | n/a |
| 5 | Capital of France | quick | success, 8 sources | 29.9s | yes |
| 6 | Long-context benchmarks 2024 | normal | timeout at 180s | >180s | n/a |
| 7 | RAG vs long context | quick | timeout at 120s | >120s | n/a |
| 8 | What is a knowledge graph | quick | success, 8 sources | 21.5s | **no** |

3/8 success. The model responds in ~30s when it works; the timeouts all hit
the depth's hard wall. Also worth noting — the agent picks `composio_web_search`
(Exa) every time and never reaches for `search_tavily` / `search_brave` /
`search_duckduckgo`, `academic_search`, `rerank_candidates`, or
`batch_get_content` despite them being available. The model is staying on the
path of least resistance.

## Behavioral observations (round 1)

### 1. The "citation bug" the `final_answer` tool exists to fix is real, and reproduces

- Call 2 (Python 3.13): `[1][2]` markers in `answer`, but `sources` list
  contains 8 URLs and there's no `[3]..[8]` in the answer — there's a
  mismatch between the synthesized text and the `sources` list. The
  `final_answer_tool_payload` in `extra` correctly includes 3 sources; the
  synthesized text only cites 2.
- Call 5 (Paris): same thing — 3 citations, 8 sources, uncited URLs.
- Call 8 (knowledge graph): no `final_answer` call at all;
  `tool_trace=["composio_web_search"]`. The runner falls back to the last
  AIMessage content. That text happens to include a numbered sources list, so
  the output looks fine here — but only because the model chose to render
  one. A harder multi-hop query would surface this.

### 2. Tool selection bias

The prompt says *"Start with broad search tools … Prefer `composio_web_search`"*,
and the model is taking that very literally — every successful call hits
exactly one tool (`composio_web_search`) and then `final_answer`. It never:

- uses `composio_similarlinks` to expand a seed URL (which the prompt
  explicitly says to do),
- uses `get_content` to actually read a page (the most useful tool in the
  whole toolbox),
- uses `academic_search` even when the query is academic-themed,
- uses `rerank_candidates` even when the result pool would obviously benefit.

The model is treating `composio_web_search` as a one-stop shop instead of a
discovery front-end. That tracks with the Exa tool returning pre-synthesized
answers + citations, which makes the rest of the toolset redundant from the
agent's perspective.

### 3. Latency is bimodal — quick burst or wall-clock timeout

- 21s, 30s, 34s on successful quick calls.
- Timeouts at 120s (quick) and 180s (normal) on the rest.

There's no graceful middle path — the runner doesn't return a partial answer,
it just `asyncio.TimeoutError` propagates and the outer `mcp.py:81-91` catch
path returns `classify_error(...).to_dict()`. The user gets nothing. Worth
considering: a `try/except` in the runner that, on timeout, returns the
partial graph and the last AIMessage rather than failing the whole call.

### 4. The `research_goal` field is not very effective

I sent distinct `research_goal` values per call but the model never visibly
changed its strategy based on them. They're concatenated into the user prompt
at `runner.py:118-123` as a single line. They get propagated to OTel
attributes and Langfuse metadata, which is useful for *post-hoc analysis*,
but the prompt doesn't give the model any special instruction to honor the
goal.

### 5. Telemetry is the strongest part of the design

- `mcp.py:46-138` wraps the call in an OTel span with `agent.depth`,
  `agent.model`, `agent.tool_calls_count`, `agent.sources_count`,
  `agent.duration_seconds` attributes — exactly what you'd want for Grafana
  slicing.
- `runner.py:222-247` emits a DuckDB `tool.agentic_web_research.response` event
  with `payload_json` containing sources, tool_trace, knowledge graph,
  uncertainties.
- `telemetry.py:1311-1327` records an OTel histogram of duration. Triple-emit
  (OTel → Grafana, Langfuse, DuckDB) is the right pattern.
- The knowledge graph is genuinely useful: it surfaces `potential_conflicts`
  (URLs seen with multiple titles) — visible in call 2 where the PEP 719 URL
  shows up twice.

### 6. The depth profile interpretation is good

`runner.py:202-208` adds a `warnings` entry when source count is below the
depth expectation (2/3/5). That's the right kind of post-run check.

### 7. The external MCP loader is overengineered for the current use case

`runner.py:81-108` parses either inline JSON or a filesystem path, then
`await`s `MultiServerMCPClient.get_tools()`. This adds an extra import path
(`langchain_mcp_adapters`) and a hidden async surface, all gated by a single
env var. Functionally fine; just noting that no other code in the project uses
this pattern, so it's a fresh dependency surface to maintain.

## Things to consider changing

1. **Make the system prompt force the agent to read at least one source.** A
   "rule" like *"Always call `get_content` on your top 1-2 candidates before
   final_answer"* would convert a 30s one-shot search into a more substantive,
   citation-correct answer. The current behavior makes the dedicated content
   tools unused.
2. **Handle `asyncio.TimeoutError` as a partial success.** If the budget is
   exhausted, return `result.answer = ""`,
   `result.warnings = ["Tool budget/timeout reached; partial sources follow"]`,
   and the partial `sources` / `tool_trace`. Don't fail the whole call.
3. **Cite the same sources that are in `final_answer_tool_payload.sources` in
   the answer text.** The `FinalAnswerInput` already gives a `sources` list —
   the runner could verify that every URL in `sources` is referenced in
   `answer` (e.g. assert N `[i]` markers) and warn otherwise. That closes the
   gap where the answer cites 2 of 8 sources.
4. **Default `temperature` is 0** (`settings.py:306`). For a ReAct agent that
   should be a tiny bit exploratory, 0.2-0.3 often improves tool diversity
   (encouraging the model to actually pick a different search tool sometimes).
5. **`agentic_research_gemini_fallback_model` defaults to
   `"gemini-3.5-flash"`** which isn't a real Gemini model string (the real
   ones are `gemini-2.0-flash`, `gemini-2.5-flash`, `gemini-1.5-flash`). The
   ChatGoogleGenerativeAI model factory will likely reject it on first
   activation. Quick fix in `settings.py:298`.
6. **`agentic_research_quick_timeout_seconds = 120`** vs. the primary model
   taking 21-34s plus cold-start — comfortable, but
   `normal_timeout_seconds = 180` is too tight for the 10-call budget when
   the model is slow. Bump to 240-300, or implement the partial-success
   behavior.
7. **The "Prefer composio_web_search" rule is too strong.** The model never
   explores `search_tavily`/`search_brave`/`search_duckduckgo` for redundancy.
   Consider rewording the prompt to *"Start with one search tool; add a
   second independent tool when you need corroboration or coverage from a
   different index."*

## Summary

The module is a clean, well-instrumented integration that follows current
LangChain `create_agent` conventions and beats the reference Tongyi design on
observability and structured output. Its main weakness is *agent guidance*:
the model is too comfortable with `composio_web_search` + `final_answer` to
actually use the broader toolset, and the system prompt doesn't push it. The
citation/source mismatch between `answer` and `sources` is a real product
bug. Latency on the working calls is fine (21-34s) but the bimodal failure
mode (clean success vs. wall-clock timeout with no partial return) is rough
on UX. The default Gemini fallback model name is wrong and should be fixed.

`runner.py:120-217`, `runner.py:159-165`, `runner.py:202-208`,
`prompts.py:30-39`, `settings.py:298`, `mcp.py:62-138` are the most
important places to focus next.
