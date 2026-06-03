# Coherent FastMCP and Tool-Surface Plan

Date: 2026-06-03

Source documents consolidated:

- `critical-analysis-fastmcp-rerank-tool-strategy-2026-06-03.md`
- `observability-action-recommendations-2026-06-03.md`
- `rag-mcp-eval-frameworks-addendum-2026-06-03.md`
- `mcp-eval-llm-judge-frameworks-research-2026-06-03.md`

## Goal

Reduce tool-surface confusion and improve agent tool choice without hiding retrieval decisions from the calling agent.

The target is a smaller, better-routed MCP tool surface using FastMCP visibility, tags, and tool search before optional CodeMode.

## Core Decisions

1. Tool visibility and tags are P0.
2. FastMCP tool search is P0.
3. Prompts/resources-as-tools are P1 compatibility features.
4. CodeMode is P2 and opt-in only.
5. Normal `web_search`, `get_content`, and answer-tool contracts remain stable.
6. Tool steering must be observable and evaluated through MCP scenarios.

## Scope

In scope:

- tool tags
- tool profiles
- FastMCP visibility
- FastMCP tool search transform
- discovery/selection observability
- MCP eval scenarios for tool choice

Out of scope:

- replacing existing tools
- hiding fetch/search orchestration inside automatic deep-research behavior
- default CodeMode for all clients
- changing user-facing tool contracts without separate migration plan

## Target Tool Profiles

### `default`

Visible by default:

- `web_search`
- `get_content`
- `batch_get_content`
- `discover_links`
- status/resources needed for health and workflow guidance

Purpose:

- normal agent-facing retrieval workflow
- cheap discovery and explicit fetch

### `research`

Includes `default` plus:

- `gemini_search`
- `perplexity_search`
- `academic_search`
- `agentic_web_research`

Purpose:

- answer synthesis
- multi-step research
- higher-cost workflows

### `media`

Includes:

- `youtube_search`
- `youtube_transcript`

Purpose:

- keep video workflows discoverable without crowding default search.

### `diagnostic`

Includes:

- status/debug resources
- analytics/eval query tools if exposed
- provider health tools if present

Purpose:

- operators and debugging agents.

### `experimental`

Includes:

- CodeMode
- new reranker/eval/admin tools
- any feature not yet stable

Purpose:

- explicit opt-in only.

## Tool Tags

Every tool should have stable tags.

Recommended tags:

- `search`
- `fetch`
- `batch`
- `answer`
- `ai_search`
- `media`
- `diagnostic`
- `status`
- `expensive`
- `experimental`
- `safe_default`

Example mapping:

| Tool | Tags |
| --- | --- |
| `web_search` | `search`, `safe_default` |
| `get_content` | `fetch`, `safe_default` |
| `batch_get_content` | `fetch`, `batch`, `safe_default` |
| `discover_links` | `fetch`, `search`, `safe_default` |
| `gemini_search` | `answer`, `ai_search`, `expensive` |
| `perplexity_search` | `answer`, `ai_search`, `expensive` |
| `youtube_search` | `search`, `media` |
| `youtube_transcript` | `fetch`, `media` |
| `agentic_web_research` | `answer`, `ai_search`, `expensive`, `experimental` |

## Phase 0: Current Surface Inventory

Before changing visibility, produce a machine-readable inventory:

- tool name
- description
- current annotations
- tags to add
- profile membership
- expensive flag
- stable/experimental status

Acceptance:

- every tool has one owner profile
- every tool has at least one functional tag
- expensive tools are explicitly marked

## Phase 1: Visibility Profiles

Implement profile selection through settings.

Conceptual config:

```text
KINDLY_TOOL_PROFILE=default|research|media|diagnostic|experimental|full
```

Rules:

- `default` is conservative.
- `full` exists for local development only.
- `experimental` is never default.
- profile selection should be visible in startup/status output.

Acceptance:

- profile controls which tools are visible.
- profile can be verified by FastMCP client tests.
- hidden tools are not advertised to normal clients.

## Phase 2: Tool Search

Add FastMCP tool search after profiles/tags.

Purpose:

- avoid injecting the full tool list into agent context
- let agents search for relevant tools
- preserve normal tool calls once selected

Recommended always-visible tools:

- `web_search`
- `get_content`
- `status` / workflow resource if exposed

Acceptance:

- a tool-search query for "find docs" surfaces `web_search`.
- a tool-search query for "fetch this URL" surfaces `get_content`.
- a tool-search query for "YouTube transcript" surfaces `youtube_search` and `youtube_transcript`.
- a tool-search query for "synthesize answer with citations" surfaces `gemini_search` or `perplexity_search` only in the right profile.

## Phase 3: Prompts And Resources As Tools

Add only for compatibility with clients that do not use MCP prompts/resources well.

Rules:

- keep native prompts/resources.
- expose compatibility wrappers only when enabled.
- tag wrappers as `compatibility`.

Acceptance:

- clients that only call tools can discover workflow guidance.
- prompt/resource wrappers do not duplicate or confuse normal tools.

## Phase 4: CodeMode

CodeMode is P2 and opt-in.

Use only for:

- coding agents
- local/dev workflows
- complex workflows where code execution reduces repeated tool calls

Do not use for:

- default clients
- non-coding agents
- simple search/fetch tasks
- hiding agent-visible retrieval decisions

Acceptance before enabling:

- profile-gated
- clear timeout/tool-call budget
- observable `CodeMode` execution events
- deterministic tests for profile exposure
- documented security and debugging behavior

## Tool-Choice Evaluation

Use `mcp-eval` / `mcpevals` for tool-surface behavior.

P0 suites:

- `known_url_get_content`
- `docs_lookup_search_then_fetch`
- `exact_literal_no_rewrite`
- `expensive_tool_overuse`
- `youtube_search_to_transcript`

DeepEval-style custom judge metrics:

- `tool_choice_correct`
- `argument_correctness`
- `tool_sequence_efficiency`
- `expensive_tool_overuse`
- `task_completion`

## Observability Requirements

Emit structured events for:

- active tool profile
- visible tool count
- hidden tool count
- tool search query
- tool search results
- selected tool
- profile-gated tool denied
- expensive tool first-use or overuse warning

Grafana panels:

- tool selection by profile
- expensive-tool usage by query type
- tool-search hit rate
- hidden/visible tool count over releases
- MCP eval pass rate for tool-choice suites

## Implementation Order

P0:

1. Build tool inventory.
2. Add tags to all tools.
3. Add profile setting and visibility gates.
4. Add tests for profile visibility.
5. Add `mcp-eval` scenarios for tool choice.

P1:

1. Add FastMCP tool search.
2. Add observability for tool search and selection.
3. Add prompts/resources-as-tools compatibility wrappers behind config.
4. Add Grafana tool-surface panels.

P2:

1. Add CodeMode opt-in profile.
2. Add CodeMode observability.
3. Add CodeMode eval cases.
4. Promote only if it reduces tool-count/context pressure without reducing correctness.

## Non-Goals

- Do not make CodeMode default.
- Do not collapse search and fetch into hidden automatic behavior.
- Do not expose every tool in every profile.
- Do not add another eval or observability platform for tool-surface work.

## Acceptance Criteria

The FastMCP/tools plan is complete when:

- every tool has tags and a profile.
- default profile is smaller than full profile.
- expensive tools are hidden or gated outside research/full profiles.
- tool search finds the correct tool for common scenarios.
- MCP eval scenarios pass for tool choice and argument correctness.
- Grafana shows tool selection and expensive-tool usage trends.

## Final Recommendation

Implement FastMCP visibility and tags first, then tool search, then compatibility wrappers, then CodeMode as an opt-in advanced profile. The stable agent-facing primitives remain `web_search`, `get_content`, and explicit answer tools; the goal is better discoverability and routing, not hidden orchestration.
