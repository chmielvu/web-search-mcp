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

## Depth modes

- `quick`: smaller tool budget, faster finish.
- `normal`: default research budget.
- `deep`: higher tool budget and longer timeout.

The depth setting only changes the agent's tool budget and timeout. It does not force arbitrary page-count limits.
