# Advanced MCP Architecture: Research Insights

This document summarizes in-depth research into two advanced areas of the Model Context Protocol (MCP) ecosystem: **MCP-specific LLM evaluations (Evals)** and the **Agent-as-a-Tool pattern**. 

## 1. MCP-Specific LLM Evaluations (LLM-as-a-judge)

Evaluating an MCP server requires tracking the full "agent trajectory" (tool discovery, schema adherence, execution grounding, and error recovery) rather than just final text generation.

### Leading Frameworks
* **`mcp-evals` (by Matthew Lenhard)**: A Node.js/GitHub Actions framework that scores tool interactions via an LLM judge on accuracy, completeness, and reasoning. Ideal for CI/CD checks on server updates.
* **`MCP-Bench` (Accenture & UC Berkeley)**: Employs a hybrid strategy using rule-based checks for JSON schema validation and LLM-as-a-judge for cross-domain task fulfillment across hundreds of tools.
* **`mcp-eval` (by lastmile-ai)**: Leverages OpenTelemetry (OTEL) execution traces as the single source of truth for evaluation, running tests against live sandboxed MCP servers rather than mocks.
* **`MCPGauge` (Academic Research)**: Evaluates proactivity, compliance, effectiveness, and overhead. It highlights the issue of "token inflation" caused by bloated tool manifests degrading agent reasoning.

### Best Practices
* **Avoid Mocks**: Test against real MCP servers to capture network latency and genuine state management issues.
* **Hybrid Evaluation**: Use deterministic assertions for structure and LLMs for semantic validation.
* **Multi-Turn Tracking**: Agents rarely succeed in one turn. Evals must track if an agent can iteratively correct bad arguments and synthesize across multiple tool hops.

---

## 2. Exposing AI Agents as MCP Tools (Agent-as-a-Tool)

The "Agent as a Tool" pattern encapsulates a full multi-step AI agent (with its own internal reasoning loops) behind a single MCP tool interface. This enables hierarchical agent architectures.

### Framework Implementations
* **LangGraph**: Natively supports exposing stateful, multi-agent workflows. When deployed via the LangGraph API, it provides a Streamable HTTP `/mcp` endpoint where the graph is automatically exposed as a tool.
* **smolagents**: While typically an MCP consumer, a `smolagents.CodeAgent` can easily be exposed by wrapping its `agent.run()` call inside a `FastMCP` tool decorator.
* **Tiny Agents**: Minimalist agents (~50 lines) that can be wrapped in standard `stdio` MCP servers to provide highly portable, local tools to a higher-level orchestrator.

### Notable GitHub Repositories
* **`lastmile-ai/mcp-agent`**: Engineered around the "server-of-servers" concept, integrating with durable runtimes (like Temporal) to handle long-running child agents without timeouts.
* **`microsoft/azure-skills` (MAF)**: Wraps internal agents as `AIFunction` objects within an `McpServerTool` for use by VS Code Copilot.
* **`Pimzino/agentic-tools-mcp`**: Provides an MCP server offering complex, agent-like tools (e.g., PRD parsing, advanced project research).
* **`langroid/langroid`**: Converts standard MCP server tools into agent tools or exposes Langroid's multi-agent flows to standard MCP clients.

---

## Application to `web-search-mcp`

1. **OTEL-Driven Evals**: Given the existing `OpenTelemetry` setup in `web-search-mcp`, we can extract OTEL traces from search sessions and use a judge LLM (e.g., via DeepEval) to automatically evaluate the quality of the Reciprocal Rank Fusion (RRF) when merging SearXNG, Gemini, and Tavily results.
2. **`deep_research` Tool**: We can introduce a new tool inside `server.py` that spins up an internal `smolagents` loop. This sub-agent would autonomously handle multi-step web scraping and pagination using the existing `web_search` and `get_content` functions, returning a fully synthesized report to the client rather than raw search results.
