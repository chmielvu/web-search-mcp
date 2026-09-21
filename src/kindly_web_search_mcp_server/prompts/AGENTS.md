<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-17 | Last verified: 2026-09-17 -->

# AGENTS.md - Prompts

Prompt templates, builders, and registry for all subsystems.

## Key Files

| File | Role |
|---|---|
| `registry.py` | Prompt registry and lookup |
| `builders.py` | Prompt-building helpers |
| `query_understanding.py` | Query understanding prompts |
| `query_rewrite.py` | Owner of rewrite templates, named-slot schema (`RewrittenQueries`), `REWRITE_PROMPT_VERSION`, and per-intent `REWRITE_INTENT_ANGLES` / `select_rewrite_prompt` |
| `query_rewrite_parallel.py` | Owner of the Parallel Search API rewrite prompt family (`PARALLEL_REWRITE_SYSTEM` / `PARALLEL_REWRITE_USER`, `PARALLEL_REWRITE_PROMPT_VERSION`, `ParallelRewrittenQueries` four-slot schema: original/refined/decomposed_1/decomposed_2); consumed by `search.quick.quick_web_search_rewrite` |
| `adaptive_search.py` | Owner of adaptive wave templates, strict response schemas (`TargetedQuery`, `FollowupBatch`, `ContinuationDecision`, `SynthesisDraft`), and `ADAPTIVE_SEARCH_PROMPT_VERSION`; consumed by `search.adaptive` |
| `rerank.py` | Reranking prompts |
| `rerank_llm.py` / `rerank_llm.yaml` | LLM rerank prompts and config |
| `entity_extraction.py` | Entity extraction prompts |
| `provider_gemini.py` | Gemini provider prompt family |
| `provider_grok.py` | Grok provider prompt family |
| `models.py` | Prompt-related models |

## Rules

- Prompt registry is versioned and used by the search pipeline.
- Prompt families are separated by task and provider (not one giant blob).
- Prompt changes must stay aligned with tests that exercise the registry.
- `rerank_llm.yaml` follows the installed RankLLM `multiturn_listwise` keys (`prefix_user`, `body_user`, `suffix_user`).
- `rerank.py` owns RankLLM's six-intent policy plus `SHARED_RANKING_INSTRUCTIONS`,
  and a separate short Voyage standing/intent instruction set
  (`build_voyage_instruction`). RankLLM still uses `build_rankllm_query`.
- RankLLM candidate XML may include published date, source, source kind, answer kind, providers, and extra evidence passages; those fields stay off the public MCP hit.
- `query_rewrite.py` owns the six-intent angle registry used by `_rewrite_queries`;
  the planner still emits the same six branches. Intent blocks are adapted from
  `query_writer_instructions` plus GitRAG / alexdong / dspy-opt / knowledge-ops /
  secondbrain / WebRAgent templates — do not invent a parallel rewrite schema.
  SERP slots are keyword bags (no operators). Tavily/Exa slot rules follow the
  official Tavily search best-practices and Exa searching.md page-description
  grammar; do not reintroduce `site:` into rewrite strings.

## Testing

```bash
uv run pytest tests/test_prompt_registry.py
uv run pytest tests/test_query_understanding.py tests/test_rerank_llm.py
```