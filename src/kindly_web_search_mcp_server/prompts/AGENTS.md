<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-21 | Last verified: 2026-09-21 -->

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
| `adaptive_search.py` | Owner of capability-focused adaptive wave templates, described strict response schemas (`TargetedQuery`, `FollowupBatch`, `ContinuationDecision`, `SynthesisDraft`), and `ADAPTIVE_SEARCH_PROMPT_VERSION`; consumed by `search.adaptive` |
| `rerank.py` | Reranking prompts |
| `rerank_llm.py` / `rerank_llm.yaml` | LLM rerank prompts and config |
| `entity_extraction.py` | Entity extraction prompts |
| `provider_gemini.py` | Gemini provider prompt family |
| `provider_grok.py` | Grok provider prompt family |
| `models.py` | Prompt-related models |

## Rules

- Prompt registry is versioned and used by the search pipeline.
- Prompt families are separated by task and provider (not one giant blob).
- Prompt schema shapes stay aligned with their production consumers; the frozen
  test suite is changed or run only when explicitly requested.
- Adaptive prompt changes are evaluated against the live Gemini-first
  `adaptive_search_llm` / `summarization` chains on fixed capability cases.
  Decision prompt v4 places a compact exact query history beside the terminal
  task and requires an unused search move with a concrete retrieval
  discriminator; rephrasing alone is not novelty. Continuation must search
  whenever a material `research_goal` requirement lacks direct evidence and an
  unused move remains; synthesis answers the full goal from citable passages.
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
- Rewrite prompt v11 treats the five slots as a retrieval portfolio: the original
  normalized query is a sixth branch the pipeline already searches, `free` is the
  refined overview, `serp1`/`serp2` are two atomic subproblems each committed to a
  different requirement or evidence class (comparisons may pair one entity with one
  requirement per slot; an entity swap repeating the same facet is a mirror),
  `semantic_tavily` asks one connective cause/change/trade-off question, and
  `semantic_exa` describes the primary-source page class. The research goal is the
  coverage specification: its requirements are allocated across slots as short
  search terms, while pasting a goal clause into a slot stays forbidden.

## Testing

The repository test suite is frozen. Do not run or modify prompt tests unless
explicitly instructed. Use throwaway live prompt evals plus Ruff, formatting,
and `ty` checks for prompt work.