# AGENTS.md - LLM

This package owns OpenAI-compatible model routing and tracing context.

## Structure

```text
llm/
|-- config.py           # Provider endpoint construction from settings
|-- models.py           # Endpoint, generation, and usage contracts
|-- router.py           # OpenAI/Hugging Face clients and sequential endpoint fallback
|-- structured.py       # Structured generation helpers
|-- worker.py           # Worker-facing generation orchestration
|-- usage.py            # Token usage normalization
`-- phoenix_tracing.py  # OpenInference context propagation
```

## Rules

- Use `openai.OpenAI` / `openai.AsyncOpenAI` for OpenAI-compatible endpoints and
  `huggingface_hub.InferenceClient` only for the Hugging Face worker.
- Set explicit request timeouts and `max_retries=0`; fallback belongs to `LLMRouter`.
- Keep provider API calls behind this package's endpoint and generation contracts.
- Do not add LiteLLM or provider-prefixed model identifiers.

## Validation

Run focused LLM checks only when validation is requested.
