# AGENTS.md - Unified Inference Subsystem

Centralized LLM, embedding, and reranking model execution through a catalog-driven engine with provider-agnostic adapters.

## Key Files

| File | Role |
|---|---|
| `types.py` | Canonical data contracts (`ModelSpec`, `ModelCapability`, `LLMUsage`, `LLMGeneration`) |
| `registry.py` | **Unified model + provider registry** — `define_model()`, `add_provider()`, `get_model()`, `get_provider()` |
| `chain.py` | `ChainSpec` and `register_chain` / `get_chain` for ordered model lists |
| `catalog.py` | Declarative model & chain registration from `settings` |
| `engine.py` | `execute_with_fallback()` — dispatches via `get_provider()` or optional `handler` |
| `router.py` | `LLMRouter` — thin wrapper around `ChainSpec` with `_complete()` |
| `worker.py` | `LLMWorker` and `StructuredLLMRequest` for structured JSON generation |
| `adapters/openai.py` | OpenAI-compatible adapter (cerebras, groq, vercel, openrouter) |
| `adapters/hf_chat.py` | Hugging Face InferenceClient adapter |
| `adapters/genai.py` | Google GenAI adapter (with `get_genai_client`) |
| `adapters/cohere.py` | Cohere rerank provider adapter |
| `adapters/voyage.py` | Voyage rerank provider adapter |
| `adapters/openrouter.py` | OpenRouter rerank provider adapter |
| `bridges/rankllm.py` | RankLLM XML listwise reranker bridge |
| `bridges/flockmtl.py` | DuckDB FlockMTL secret bridge |

## Architecture

```
types.py (ModelSpec, ModelCapability, LLMGeneration)  ← canonical contracts
    ↓
registry.py (define_model, add_provider, get_model, get_provider)
  - ModelDefinition: canonical_id, display_name, capabilities
  - ProviderConfig:  model_id, base_url, api_key_env, timeout
  - ProviderAdapter: execute callable registered by name
    ↓
chain.py (ChainSpec, register_chain, get_chain)  ← ordered "canonical_id@provider" refs
    ↓
engine.py (execute_with_fallback)  ← retry + timeout + telemetry + dispatch
    ↓
adapters/*.py  ← provider-specific I/O (self-register on import)
bridges/*.py  ← Domain-specific integrations
    ↓
router.py (LLMRouter) + worker.py (LLMWorker, StructuredLLMRequest)
```

## Unified Registry (`registry.py`)

The registry replaces the old `model_registry.py` + `provider_registry.py` with a single
self-documenting file.  Every model is defined **once** as a canonical entry, then
associated with one or more providers that can serve it.

### Model Definition

```python
define_model(
    "gpt-oss-120b",
    display_name="GPT OSS 120B",
    description="Primary worker LLM — fast, cheap, OpenAI-compatible.",
    capabilities={ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT},
)
add_provider(
    "gpt-oss-120b", "cerebras",
    as_openai(
        model_id="gpt-oss-120b",
        base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        cost_per_1m_input=0.35,
        cost_per_1m_output=0.75,
    ),
)
```

### Chain Reference Format

Chains reference models as `"canonical_id@provider"`:

```python
register_chain("worker_llm", [
    "gpt-oss-120b@cerebras",
    "gpt-oss-120b@groq",
    "gpt-oss-120b@huggingface",
    "gpt-oss-120b@vercel",
])
```

### Provider Config Helpers

| Helper | Provider Family | Key Quirk |
|---|---|---|
| `as_openai()` | Cerebras, Groq, Vercel, OpenRouter (chat) | OpenAI-compatible API |
| `as_google()` | Gemini | SDK handles endpoint, no base_url |
| `as_huggingface()` | HuggingFace | sync InferenceClient in thread |
| `as_rerank()` | Cohere, Voyage, OpenRouter (rerank) | HTTP-based, not OpenAI-compatible |
| `as_embedding()` | HuggingFace embeddings | feature extraction pipeline |

### Cross-Provider Model ID Normalization

Different providers expose the same model under different names.  The registry
provides built-in normalization:

| Provider | Model ID | Canonical (after normalization) |
|---|---|---|
| Cerebras | `gpt-oss-120b` | `gpt-oss-120b` |
| Groq | `openai/gpt-oss-120b` | `gpt-oss-120b` |
| HuggingFace | `openai/gpt-oss-120b:nscale` | `gpt-oss-120b:nscale` |

- `normalize_model_id("openai/gpt-oss-120b")` → `"gpt-oss-120b"` strips known prefixes
- `resolve_model_id("gpt-oss-120b", "cerebras")` → `"gpt-oss-120b"` returns provider-specific string

### Live Provider Model Inventory

The 2026-07-25 provider refresh confirmed these active text-output models:

| Provider | Model IDs registered in the catalog |
|---|---|
| Cerebras | `zai-glm-4.7`, `gemma-4-31b` |
| Groq | `groq/compound`, `groq/compound-mini`, `llama-3.1-8b-instant`, `openai/gpt-oss-20b`, `allam-2-7b`, `llama-3.3-70b-versatile`, `openai/gpt-oss-120b`, `qwen/qwen3.6-27b` |

The existing `gpt-oss-120b` and `gpt-oss-20b` entries were corrected to the
provider-reported GPT OSS names.  Groq's Prompt Guard and GPT OSS Safeguard
models are intentionally excluded because they are moderation/safety models;
speech-output Orpheus models and audio-input Whisper transcription models are
also excluded because the generic OpenAI-compatible adapter exposes chat/text
generation only.  The live list contained no active Llama 4 model, and both
known Llama 4 retrieve checks returned 404.

### Multiple API Keys via Qualified Provider Keys

The same model can be served by the same provider with different API keys
or timeouts.  Use a `:` suffix on the provider name to create distinct
configurations that share one model definition and one adapter:

```
define_model("gemini-3.1-flash-lite", capabilities={CHAT, GROUNDING, ...})
add_provider("gemini-3.1-flash-lite", "google",        as_google(..., api_key_env="GEMINI_API_KEY"))
add_provider("gemini-3.1-flash-lite", "google:second", as_google(..., api_key_env="SECOND_GEMINI_API_KEY"))
add_provider("gemini-3.1-flash-lite", "google:rankllm", as_google(..., default_timeout=20.0))
```

Chain references use the qualified key: `"gemini-3.1-flash-lite@google:second"`.
The engine resolves `"google:second"` → adapter `"google"` automatically.

The worker and classifier chains use the same pattern for provider key
failover.  `@cerebras:second` reads `SECOND_CEREBRAS_API_KEY`, while
`@groq:second` reads `SECOND_GROQ_API_KEY`; the primary key entries remain
`CEREBRAS_API_KEY` and `GROQ_API_KEY`.  Secondary entries are attempted
immediately after their corresponding primary and before cross-provider
fallbacks.

This eliminates the old pattern of defining the same model N times with
different canonical IDs just to vary the API key or timeout.

### Provider Adapter Aliasing

Multiple provider names can share one adapter implementation:

```python
register_provider_adapter(ProviderAdapter(name="openai", execute=execute_openai, ...))
register_provider_alias("cerebras", "openai")
register_provider_alias("groq", "openai")
```

Chat OpenRouter uses the shared `openai` adapter under the `openrouter` name;
OpenRouter's `/rerank` HTTP adapter is registered separately as
`openrouter_rerank` so importing adapters cannot overwrite the chat alias.

## Fallback Pattern

```
chain.get_chain("name") → ChainSpec(models=[...])
engine.execute_with_fallback(chain, operation, **kwargs) → tries primary → each fallback → raises ChainExhaustedError
```

- The engine resolves `get_provider(spec.provider)` to find the adapter.
- Non-retryable errors (auth, permission, bad request, not found, validation, and local configuration errors) abort immediately; retryable errors (rate limit, connection, server error, conflict, and timeout) try the next spec.
- Telemetry (`LLMUsage`, `LLMGeneration`) captured per attempt by provider adapters.
- Domain bridges may impose a total chain budget in addition to per-provider
  timeouts; canceled provider tasks must be awaited and drained before failure
  is returned to an MCP tool.

## Catalog Chains

| Chain Name | Primary | Fallbacks |
|---|---|---|
| `worker_llm` | gpt-oss-120b@cerebras | gpt-oss-120b@cerebras:second → zai-glm-4.7@cerebras → @cerebras:second → gemma-4-31b@cerebras → @cerebras:second → @groq → @groq:second → @huggingface → @vercel |
| `classifier_llm` | gpt-oss-20b@groq | @groq:second → @vercel |
| `cross_encoder_rerank` | rerank-v4@cohere | @openrouter_rerank → @voyage |
| `gemini_grounding` | gemini-3.1-flash-lite@google:second | gemini-2.5-flash@google → gemini-2.5-flash-lite@google |
| `rankllm` | gemini-3.5-flash-lite@google:rankllm | gemini-3.1-flash-lite@google:rankllm → rankllm-openrouter@openrouter |
| `summarization` | gemini-3.5-flash-lite@google | gemini-3.1-flash-lite@google → gemma-4-26b-a4b-it@google |
| `embedding` | multilingual-e5-large-instruct@huggingface | — |

Qualified keys: `@google:second` uses `SECOND_GEMINI_API_KEY` and transparently accepts the existing `GEMINI_SECOND_API_KEY` spelling; `@google:rankllm` uses the rankllm timeout; `@cerebras:second` and `@groq:second` use their corresponding `SECOND_*_API_KEY` variables.

Cerebras `zai-glm-4.7` and `gemma-4-31b` use the OpenAI-compatible Chat
Completions message roles documented by Cerebras.  The OpenAI adapter removes
the GPT OSS-only `Reasoning:` Harmony directive for those models and forwards
`reasoning_effort` to their documented Cerebras controls; GPT OSS keeps its
existing Harmony-compatible path.

## Key Design Decisions

- **No `LLMEndpoint`** — `ModelSpec` is the single canonical model representation.
- **No `FallbackChainSpec`** — `ChainSpec` with ordered `model_spec_ids` replaces it.
- **No `build_worker_endpoints`** — Use `build_worker_router()` instead.
- **`LLMGeneration.spec`** — replaces `LLMGeneration.endpoint`. Access with `gen.spec.provider` / `gen.spec.model_id`.
- **Provider adapters self-register** — imported by `adapters/__init__.py` to call `register_provider_adapter()`.
- **Models register from `settings`** — `catalog._register_all()` is called at module import time.
- **`registry.py` uses `RLock`** (reentrant) because `_get_model_def()` is called from within locked contexts in `add_provider()` and `get_model()`.

## Run Context

`engine.bind_run_context(tool_call_id, operation)` sets a thread-local context consumed by downstream LLM calls for OTEL span correlation. `engine.reset_run_context(token)` restores previous state.

## Testing

```bash
uv run pytest tests/test_inference_subsystem.py tests/test_llm_router.py -v
```