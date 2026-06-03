# Addendum: Lightweight RAG and MCP Evaluation Frameworks

Date researched: 2026-06-03T11:26:07+02:00

Superseded: use `plans/IN-DESIGN/observability/mcp-eval-llm-judge-frameworks-research-2026-06-03.md` as the coherent source of truth. The current decision is one added framework (`mcp-eval` / `mcpevals`) plus custom DeepEval-style LLM-as-judge metrics over DuckDB, Langfuse, and Grafana. Do not implement the multi-framework stack described below.

Purpose: identify practical evaluation libraries and frameworks for the Kindly web-search MCP roadmap, with emphasis on lightweight RAG evaluation, LLM-as-judge, and MCP-specific tool-use evaluation.

## Recommendation Summary

Use three layers, not one framework:

1. **In-repo deterministic eval harness** for candidate survival, MRR/nDCG, latency, cache hit behavior, provider survival, and known golden cases.
2. **DeepEval or Ragas** for lightweight RAG-style LLM-as-judge metrics over `web_search`, `get_content`, and reranked outputs.
3. **mcp-eval or DeepEval MCP metrics** for MCP-specific tool-use correctness, argument correctness, path efficiency, and task completion.

Best default for this repo:

- **DeepEval** if one library must cover both RAG and MCP evals.
- **Ragas** if the immediate goal is pure RAG retrieval/generation quality.
- **mcp-eval** if the immediate goal is MCP server/agent scenario testing with tool-call assertions and OpenTelemetry-backed reports.
- **Promptfoo** for declarative MCP red-team/regression tests and provider comparisons.
- **Phoenix** or existing Langfuse/Grafana traces for production trace sampling and eval dashboards, not as the first test harness.

## RAG Evaluation Libraries

| Framework | Best Use | Fit For This Repo | Caveats |
| --- | --- | --- | --- |
| Ragas | RAG metrics and testset/eval workflows | Good for `web_search` + `get_content` quality, context precision/recall, faithfulness, response relevancy | Less MCP-specific; still needs trace/tool-call wrapper |
| DeepEval | Pytest-like LLM app evals, RAG metrics, custom G-Eval/DAG metrics | Strongest single-package candidate because it supports both RAG and MCP metrics | Cloud reporting is optional/recommended; keep local-first mode for CI |
| TruLens | RAG Triad and trace-linked evals | Useful conceptual model: context relevance, groundedness, answer relevance | Heavier observability/eval stack than needed for first harness |
| Arize Phoenix | OTEL tracing, evals, datasets, experiments | Strong if traces become the primary eval substrate; can run LLM/code/human evals over spans | Overlaps with existing Grafana/Langfuse/DuckDB stack |
| LlamaIndex evals | Faithfulness/relevancy/retrieval eval if using LlamaIndex pipelines | Useful as reference; not a natural fit unless repo adopts LlamaIndex objects | Avoid framework migration just for evals |
| Promptfoo | Declarative prompt/agent/RAG tests, red-team, provider comparison | Good for smoke/regression and MCP provider tests via YAML | JavaScript/Node dependency; MCP provider expects JSON tool-call style prompts |

### Ragas

Ragas exposes RAG metrics including context precision, context recall, context entities recall, noise sensitivity, response relevancy, and faithfulness. It also includes agent/tool-use metric categories. This maps cleanly to:

- `web_search`: context precision and source usefulness over SERP candidates.
- `get_content`: context recall and faithfulness against extracted markdown.
- reranker bakeoff: compare before/after rerank candidate sets.

Use Ragas when the question is: "Did retrieval and context improve the answer?"

### DeepEval

DeepEval is the best single-framework candidate. It supports RAG metrics such as answer relevancy, faithfulness, contextual relevancy, contextual recall, and contextual precision. It also supports custom LLM-as-judge metrics through G-Eval and DAG metrics, with pytest-style CI ergonomics.

DeepEval is especially relevant because it now has MCP-specific docs and metrics:

- `MCPUseMetric` for single-turn MCP evaluation.
- `MultiTurnMCPUseMetric` for multi-turn MCP tool use.
- `MCPTaskCompletionMetric` for task completion.
- It evaluates primitive/tool usage and argument correctness.

Use DeepEval when the question is: "Did the MCP host use the right primitive with the right arguments and complete the task?"

### TruLens

TruLens' RAG Triad is still the cleanest conceptual decomposition:

- context relevance
- groundedness
- answer relevance

This is a good rubric for custom judges even if the repo does not install TruLens.

### Phoenix

Phoenix is built on OpenTelemetry/OpenInference and supports tracing, evaluations, prompt management, datasets, and experiments. It can score traces and spans with LLM-based evaluators, code checks, or human labels. This fits the repo's existing observability direction, but it overlaps with current Langfuse/Grafana/DuckDB work.

Use Phoenix if the repo wants a dedicated eval UI over OTEL traces. Otherwise, reuse DuckDB/Langfuse/Grafana first.

### Promptfoo

Promptfoo now has an MCP provider and an MCP server integration:

- MCP provider: treats the MCP server itself as the system under test.
- Supports local/remote MCP servers.
- Supports response transforms for structured content.
- Handles connection, tool-not-found, tool execution, and timeout errors.
- Promptfoo MCP server exposes eval tools to AI agents.

Use Promptfoo for declarative regression suites, red-team tests, and model/provider comparisons. It is less ideal as the primary Python-native harness.

## MCP-Specific Evaluation Frameworks

| Framework / Benchmark | Best Use | Fit For This Repo | Caveats |
| --- | --- | --- | --- |
| mcp-eval / mcpevals | MCP server and agent scenario testing | Strongest MCP-native candidate for this repo | Young ecosystem, but aligned with OTEL and pytest/scenario workflows |
| DeepEval MCP metrics | Single/multi-turn MCP primitive use and task completion | Best if also using DeepEval for RAG metrics | Requires explicit capture of MCP tools/resources/prompts called |
| Promptfoo MCP provider | Declarative MCP server testing/red-team | Useful for adversarial and CI smoke tests | More config/YAML/Node-heavy |
| mcpx-eval | Open-ended tool-use eval with LLM judge | Good pattern reference for judge prompt shape | Focused on mcp.run ecosystem; not necessarily general-purpose |
| MCP-Bench / LiveMCPBench / MCPToolBench++ | Research benchmarks for tool-use ability | Useful methodology, not a direct project harness | Benchmark agents/models, not this server's release quality |
| MCP Inspector | Manual protocol debugging | Useful for smoke/manual checks | Not an eval framework |

### mcp-eval

`mcp-eval` is the strongest MCP-specific practical framework found. It tests MCP servers and agents, runs scenarios, captures telemetry, and supports assertions for:

- tool calls
- content checks
- performance gates
- path efficiency
- LLM judges
- JSON/HTML/Markdown reports
- OpenTelemetry-backed traces
- pytest/decorator/dataset test styles

This maps directly to Kindly:

- Ensure `web_search` is called for discovery tasks.
- Ensure `get_content` is called only after known URL selection.
- Ensure `perplexity_search` or `gemini_search` is not overused for cheap discovery.
- Validate `rewrite=false` for exact literals.
- Validate tool-call budget and latency gates.

### DeepEval MCP

DeepEval's MCP docs explicitly support single-turn and multi-turn MCP evals. It tracks MCP servers, available tools, tools called, arguments, and results, then uses MCP metrics. This is an excellent bridge between RAG eval and MCP eval if the repo wants one Python library.

Recommended first DeepEval MCP metrics:

- primitive/tool usage correctness
- argument correctness
- task completion
- multi-turn MCP use

### Promptfoo MCP

Promptfoo is useful when the eval should be declarative and CI-friendly. Its MCP provider can target local or remote MCP servers and can transform structured MCP responses before scoring. This is a good fit for:

- red-team/security probes
- prompt-injection tests
- exact expected output tests
- comparing model/tool-call behavior across providers

Do not make Promptfoo the primary harness if the repo wants Python-native, DuckDB-backed eval artifacts.

## Recommended Eval Architecture For Kindly

### Layer 1: Deterministic In-Repo Metrics

Store each eval case in DuckDB or JSONL with:

- `case_id`
- `query`
- `research_goal`
- `query_type`
- expected source domains/URLs when available
- allowed/disallowed tools
- provider set
- candidate list before rerank
- candidate list after rerank
- final tool output
- latency/cost/token fields

Metrics:

- MRR@5
- nDCG@10
- top-k exact expected URL/domain hit
- duplicate/domain diversity
- candidate survival by provider
- rewrite latency
- rerank latency
- tool count
- cache hit/miss
- failure/timeout rate

This layer should run without an LLM judge.

### Layer 2: RAG/Output Judge

Use DeepEval or Ragas over cases where there is an answer or content artifact.

Recommended metrics:

- context precision/relevancy: did the candidates/context match the query?
- context recall: did retrieved content contain needed facts?
- faithfulness/groundedness: did answer stay within retrieved content?
- answer relevancy: did output answer the question?
- noise sensitivity: did irrelevant snippets poison selection?

### Layer 3: MCP Tool-Use Judge

Use mcp-eval or DeepEval MCP metrics over scripted scenarios.

Recommended scenario classes:

- docs lookup should use `web_search` then `get_content`
- exact stack trace should use `rewrite=false`
- known URL should go straight to `get_content`
- broad topic should allow rewrite and rerank
- expensive AI-search tools should only fire when synthesis is requested
- YouTube tasks should route `youtube_search` -> `youtube_transcript`
- broken provider should return recoverable tool error, not crash

Judges should score:

- tool choice
- argument correctness
- sequence correctness
- excessive tool calls
- task completion
- recoverability/error handling

## Implementation Recommendation

P0:

1. Build the deterministic eval dataset and runner in-repo.
2. Add DeepEval for RAG metrics and a small number of custom G-Eval/DAG judges.
3. Add `mcp-eval` for MCP tool-use regression tests.
4. Persist eval outputs into existing DuckDB analytics tables or a new `eval_runs` / `eval_cases` schema.

P1:

1. Add Ragas if DeepEval's RAG metrics are not enough or if Ragas testset generation is useful.
2. Add Promptfoo for red-team/provider comparison, especially prompt-injection and MCP misuse tests.
3. Add Phoenix only if a dedicated eval UI over traces is needed beyond Langfuse/Grafana.

P2:

1. Use MCP-Bench, LiveMCPBench, and MCPToolBench++ as methodology references for broader model/tool-use benchmarking.
2. Add Flock local DuckDB LLM judge experiments for batch SQL scoring.
3. Add human-label workflow for the highest-value cases.

## Practical Default Stack

For this repo, the most pragmatic stack is:

- **DuckDB eval tables** for durable local truth.
- **DeepEval** for RAG metrics plus MCP metrics.
- **mcp-eval** for MCP server/agent scenario tests and OTEL-backed tool assertions.
- **Promptfoo** only for declarative red-team/regression suites.
- **Ragas** as a specialized add-on if RAG-specific metrics/testset generation outperform DeepEval in practice.

This avoids a heavy platform migration and fits the current observability stack.

## Sources

- Ragas metrics: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/
- Ragas eval quickstart: https://docs.ragas.io/en/stable/getstarted/evals/
- DeepEval RAG quickstart: https://deepeval.com/docs/getting-started-rag
- DeepEval metrics overview: https://deepeval.com/docs/metrics-introduction
- DeepEval MCP quickstart: https://deepeval.com/docs/getting-started-mcp
- TruLens RAG Triad: https://www.trulens.org/getting_started/core_concepts/rag_triad/
- Phoenix docs: https://arize.com/docs/phoenix
- mcp-eval GitHub: https://github.com/lastmile-ai/mcp-eval
- mcp-agent mcp-eval docs: https://docs.mcp-agent.com/test-evaluate/mcp-eval
- Promptfoo MCP provider: https://www.promptfoo.dev/docs/providers/mcp/
- Promptfoo MCP server: https://www.promptfoo.dev/docs/integrations/mcp-server/
- mcpx-eval blog: https://docs.mcp.run/blog/2025/03/03/introducing-mcpx-eval/
- MCP-Bench: https://arxiv.org/abs/2508.20453
- LiveMCPBench: https://huggingface.co/papers/2508.01780
- MCPToolBench++: https://mcpbr.org/mcptoolbench
