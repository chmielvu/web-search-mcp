# Agent Documentation

This note describes the agent-related code added in this pass.

## Where it lives

- `src/kindly_web_search_mcp_server/agent/`

## What was added

- `config.py`: runtime settings for the agent model, endpoint, temperature, timeouts, and depth budgets.
- `models.py`: Pydantic request/result models plus input schemas for the tools.
- `prompts.py`: the system prompt builder used by the ReAct runner.
- `search_tools.py`: direct search wrappers for DuckDuckGo, Tavily, Brave, Composio web search, Composio similar-links, and Composio image search.
- `content_tools.py`: fetch and discovery wrappers for `get_content`, `batch_get_content`, and `discover_links`.
- `academic_tools.py`: academic search wrapper.
- `rerank_tools.py`: reranking wrapper around the existing rerank pipeline.
- `toolset.py`: one place that collects the tools for the agent.
- `model.py`: NanoGPT-backed `ChatOpenAI` configuration.
- `knowledge_graph.py`: an in-memory NetworkX graph used for per-run reasoning summaries.
- `runner.py`: the LangChain/LangGraph ReAct runner that invokes the model and tools.
- `mcp.py`: MCP registration for `agentic_web_research`.

## MCP tool

- `agentic_web_research(query, research_goal=None, depth="normal")`

## Model and endpoint

- Model name: `Alibaba-NLP/Tongyi-DeepResearch-30B-A3B`
- Base URL: `https://nano-gpt.com/api/subscription/v1`
- API key env var: `NANOGPT_API_KEY`

## Depth settings

- `quick`
- `normal`
- `deep`

These settings change tool budgets and timeouts in the runner.

## Verification that was run

- `py_compile` on the new agent modules
- `ruff check` on the new agent modules and tests
- `pytest tests/test_agentic_web_research.py tests/test_server.py tests/test_tool_descriptions.py -q`

## Notes

- The agent uses the existing search, fetch, and rerank code from the server package.
- The graph is in-memory only in the current implementation.
- The code and tests currently refer to the agent as a package under `kindly_web_search_mcp_server`.
