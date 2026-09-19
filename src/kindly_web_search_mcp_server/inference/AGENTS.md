<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-16 | Last verified: 2026-09-16 -->

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
| `adapters/openai.py` | OpenAI-compatible adapter (groq, vercel, openrouter) |
| `adapters/hf_chat.py` | Hugging Face InferenceClient adapter |
| `adapters/genai.py` | Google GenAI adapter (with `get_genai_client`) |
| `adapters/voyage.py` | Voyage rerank provider adapter |
| `bridges/rankllm.py` | RankLLM XML listwise reranker bridge |

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
router.py (LLMRouter)
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
    "gpt-oss-120b",
    "groq",
    as_openai(
        model_id="openai/gpt-oss-120b",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        cost_per_1m_input=0.15,
        cost_per_1m_output=0.60,
    ),
)
```

### Chain Reference Format

Chains reference models as `"canonical_id@provider"`:

```python
register_chain(
    "worker_llm",
    [
        "gpt-oss-120b@groq",
        "qwen/qwen3.8-27b@groq",
        "qwen/qwen3.6-27b@groq",
        "gpt-oss-120b@vercel",
        "gpt-oss-120b@huggingface",
    ],
)
```

### Provider Config Helpers

| Helper | Provider Family | Key Quirk |
|---|---|---|
| `as_openai()` | Groq, Vercel, OpenRouter (chat) | OpenAI-compatible API |
| `as_google()` | Gemini | SDK handles endpoint, no base_url |
| `as_huggingface()` | HuggingFace | sync InferenceClient in thread |
| `as_rerank()` | Voyage (rerank) | voyageai SDK via `asyncio.to_thread` |
| `as_embedding()` | HuggingFace embeddings | feature extraction pipeline |

### Cross-Provider Model ID Normalization

Different providers expose the same model under different names.  The registry
provides built-in normalization:

| Provider | Model ID | Canonical (after normalization) |
|---|---|---|
| Groq | `openai/gpt-oss-120b` | `gpt-oss-120b` |
| HuggingFace | `openai/gpt-oss-120b:nscale` | `gpt-oss-120b:nscale` |

- `normalize_model_id("openai/gpt-oss-120b")` → `"gpt-oss-120b"` strips known prefixes
- `resolve_model_id("gpt-oss-120b", "groq")` → `"openai/gpt-oss-120b"` returns provider-specific string

### Live Provider Model Inventory

The 2026-07-25 provider refresh confirmed these active text-output models
(llama entries removed 2026-09-08 — dropped from Groq's roster; `qwen/qwen3.8-27b`
added 2026-09-08):

| Provider | Model IDs registered in the catalog |
|---|---|
| Groq | `groq/compound`, `groq/compound-mini`, `openai/gpt-oss-20b`, `allam-2-7b`, `openai/gpt-oss-120b`, `qwen/qwen3.6-27b`, `qwen/qwen3.8-27b` |

The existing `gpt-oss-120b` and `gpt-oss-20b` entries were corrected to the
provider-reported GPT OSS names.  Groq's Prompt Guard and GPT OSS Safeguard
models are intentionally excluded because they are moderation/safety models;
speech-output Orpheus models and audio-input Whisper transcription models are
also excluded because the generic OpenAI-compatible adapter exposes chat/text
generation only.

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
failover.  `@groq:second` reads `SECOND_GROQ_API_KEY`; the primary key
entry remains `GROQ_API_KEY`.  Secondary entries are attempted immediately
after their corresponding primary and before cross-provider fallbacks.

This eliminates the old pattern of defining the same model N times with
different canonical IDs just to vary the API key or timeout.

> **Gotcha — `google.genai` SDK Precedence:** When both `GOOGLE_API_KEY` and `GEMINI_API_KEY` are present in `os.environ`, the `google.genai` SDK automatically defaults to `GOOGLE_API_KEY`. If `GOOGLE_API_KEY` belongs to a GCP project without the Generative Language API enabled, requests will fail with `403 SERVICE_DISABLED`. Ensure either `GOOGLE_API_KEY` is enabled for Gemini or only `GEMINI_API_KEY` is exported.

### Provider Adapter Aliasing

Multiple provider names can share one adapter implementation:

```python
register_provider_adapter(ProviderAdapter(name="openai", execute=execute_openai, ...))
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
- Non-retryable errors (422 validation and local configuration errors) abort immediately; retryable errors (auth 401/403, bad request 400, not found 404, rate limit, quota/payment 402, connection, server error, conflict, and timeout) try the next spec.
- Telemetry (`LLMUsage`, `LLMGeneration`) captured per attempt by provider adapters.
- Domain bridges may impose a total chain budget in addition to per-provider
  timeouts; canceled provider tasks must be awaited and drained before failure
  is returned to an MCP tool.
- Voyage adapter calls `voyageai.Client.rerank` (`truncation=True`, `max_retries=0`)
  on a worker thread and serializes `{index, relevance_score}` from `results`.

## Catalog Chains

| Chain Name | Primary | Fallbacks |
|---|---|---|
| `worker_llm` | gpt-oss-120b@groq | @groq:second → @huggingface → @vercel |
| `classifier_llm` | gpt-oss-20b@groq | @groq:second → @vercel |
| `adaptive_search_llm` | gemini-3.5-flash-lite@google | gemini-3.5-flash-lite@google:second → gemini-3.1-flash-lite@google:second → gemini-2.5-flash@google → gpt-oss-120b@vercel |
| `cross_encoder_rerank` | voyage-rerank@voyage | voyage-rerank-lite@voyage |
| `gemini_grounding` | gemini-3.1-flash-lite@google:second | gemini-2.5-flash@google → gemini-2.5-flash-lite@google |
| `rankllm` | gemini-3.5-flash-lite@google:rankllm | gemini-3.1-flash-lite@google:rankllm → rankllm-openrouter@openrouter |
| `summarization` | gemini-3.5-flash-lite@google | gemini-3.1-flash-lite@google → gemma-4-26b-a4b-it@google |
| `embedding` | multilingual-e5-large-instruct@huggingface | — |

The `adaptive_search_llm` chain serves the two adaptive web-search decision stages (`search/adaptive.py::propose_followups`, `decide_continuation`) via `build_adaptive_router()`. It is Gemini-first with a 100k-token feedback budget (ranked slate + long passages) and carries no Groq entries, so adaptive traffic never contends with the worker/classifier ~7k TPM window. It tries `gemini-3.5-flash-lite` on both Gemini keys first (`@google`, then `@google:second`) for key-level failover before stepping down models. Vercel gpt-oss-120b is the terminal fallback only. The inference engine honors the caller's `timeout_seconds` per attempt (adaptive decisions: 60s), falling back to each entry's catalog default.

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