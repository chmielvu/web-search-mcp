# AGENTS.md - Prompts

Prompt templates, builders, and registry for all subsystems.

## Key Files

| File | Role |
|---|---|
| `registry.py` | Prompt registry and lookup |
| `builders.py` | Prompt-building helpers |
| `query_understanding.py` | Query understanding prompts |
| `query_rewrite.py` | Query rewrite prompt templates & intent-specific guidance (`SPECIALIZED_REWRITE_GUIDANCE`) |
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
- `rerank.py` owns the canonical six-intent instruction registry and shared
  ranking hierarchy used by cross-encoder, Voyage, RankLLM, and relevance
  query builders; keep its exact contract covered by `tests/test_rerank_prompt.py`.

## Testing

```bash
uv run pytest tests/test_prompt_registry.py
uv run pytest tests/test_query_understanding.py tests/test_rerank_llm.py
```