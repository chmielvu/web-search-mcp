# AGENTS.md - LLM

OpenAI-compatible model routing with sequential endpoint fallback.

## Key Files

| File | Role |
|---|---|
| `router.py` | `LLMRouter` — sequential fallback: Cerebras → Groq → HF → Vercel |
| `config.py` | Provider endpoint construction from settings |
| `models.py` | `LLMEndpoint`, `LLMGeneration` contracts |
| `structured.py` | Structured generation (Pydantic extraction) |
| `worker.py` | Worker-facing generation helpers |
| `usage.py` | Token/cost normalization |
| `phoenix_tracing.py` | OpenInference context propagation |

## Rules

- Use `openai.OpenAI` / `openai.AsyncOpenAI` for OpenAI-compatible endpoints.
  Use `huggingface_hub.InferenceClient` only for the HF worker.
- Set explicit request timeouts and `max_retries=0`; fallback belongs to `LLMRouter`.
- Keep provider API calls behind this package's endpoint and generation contracts.
- Do **NOT** add LiteLLM or provider-prefixed model identifiers.
- `bind_run_context(run_key, operation)` / `reset_run_context(token)` — ContextVars
  set at `tools/search.py::web_search` entry (with `finally` reset).
- Every successful `_complete` invocation writes a row to `llm_call_log`.

## Testing

```bash
uv run pytest tests/test_llm_router.py
```