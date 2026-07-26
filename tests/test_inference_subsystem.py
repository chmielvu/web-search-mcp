"""Unit tests for the inference catalog and fallback engine."""

import asyncio
from types import SimpleNamespace
import pytest
from kindly_web_search_mcp_server.inference import (
    ChainExhaustedError,
    ChainSpec,
    ExecutionResult,
    ModelSpec,
    execute_with_fallback,
    get_chain,
)
from kindly_web_search_mcp_server.inference.engine import _is_retryable_error
from kindly_web_search_mcp_server.inference.registry import (
    ProviderAdapter,
    ProviderConfig,
    add_provider,
    define_model,
    get_provider,
    register_provider_adapter,
    _PROVIDER_ADAPTERS,
)
from kindly_web_search_mcp_server.inference.types import LLMGeneration


def test_get_chain_valid():
    chain = get_chain("worker_llm")
    assert chain.name == "worker_llm"
    assert chain.primary.provider == "cerebras"
    assert len(chain.fallbacks) == 9
    assert [spec.api_key_env for spec in chain.models] == [
        "CEREBRAS_API_KEY",
        "SECOND_CEREBRAS_API_KEY",
        "CEREBRAS_API_KEY",
        "SECOND_CEREBRAS_API_KEY",
        "CEREBRAS_API_KEY",
        "SECOND_CEREBRAS_API_KEY",
        "GROQ_API_KEY",
        "SECOND_GROQ_API_KEY",
        "HF_TOKEN",
        "AI_GATEWAY_API_KEY",
    ]
    assert [spec.model_id for spec in chain.models[:6]] == [
        "gpt-oss-120b",
        "gpt-oss-120b",
        "zai-glm-4.7",
        "zai-glm-4.7",
        "gemma-4-31b",
        "gemma-4-31b",
    ]


def test_get_chain_invalid():
    with pytest.raises(KeyError):
        get_chain("nonexistent_chain")


def test_rankllm_chain_has_correct_order():
    chain = get_chain("rankllm")
    assert chain.primary.model_id == "gemini-3.5-flash-lite"
    assert [spec.model_id for spec in chain.fallbacks] == [
        "gemini-3.1-flash-lite",
        "nvidia/nemotron-3-nano-30b-a3b:free",
    ]


def test_summarization_chain_has_correct_order():
    chain = get_chain("summarization")
    assert chain.primary.model_id == "gemini-3.5-flash-lite"
    assert [spec.model_id for spec in chain.fallbacks] == [
        "gemini-3.1-flash-lite",
        "gemma-4-26b-a4b-it",
    ]


def test_no_duplicate_model_definitions():
    from kindly_web_search_mcp_server.inference.registry import list_models

    all_models = list_models()
    ids = [m.canonical_id for m in all_models]
    dupes = [mid for mid in ids if ids.count(mid) > 1]
    assert not dupes, f"Duplicate model definitions: {dupes}"


def test_live_provider_model_entries_match_api_inventory():
    from kindly_web_search_mcp_server.inference.registry import get_model, list_models

    definitions = {model.canonical_id: model for model in list_models()}
    assert {
        "zai-glm-4.7",
        "gemma-4-31b",
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "groq/compound",
        "groq/compound-mini",
        "allam-2-7b",
        "qwen/qwen3.6-27b",
    } <= definitions.keys()
    assert (
        not {
            "meta-llama/llama-prompt-guard-2-86m",
            "meta-llama/llama-prompt-guard-2-22m",
            "openai/gpt-oss-safeguard-20b",
        }
        & definitions.keys()
    )
    assert definitions["gpt-oss-120b"].display_name == "GPT OSS 120B"
    assert definitions["gpt-oss-20b"].display_name == "GPT OSS 20B"
    assert get_model("zai-glm-4.7@cerebras").model_id == "zai-glm-4.7"
    assert get_model("gemma-4-31b@cerebras").model_id == "gemma-4-31b"
    assert get_model("llama-3.1-8b-instant@groq").model_id == "llama-3.1-8b-instant"
    assert get_model("llama-3.3-70b-versatile@groq").model_id == "llama-3.3-70b-versatile"
    assert get_model("groq/compound@groq").model_id == "groq/compound"
    assert get_model("qwen/qwen3.6-27b@groq").model_id == "qwen/qwen3.6-27b"


def test_qualified_provider_key_resolves_adapter():
    from kindly_web_search_mcp_server.inference.registry import get_model

    spec = get_model("gemini-3.1-flash-lite@google:second")
    assert spec.provider == "google"
    assert spec.api_key_env == "SECOND_GEMINI_API_KEY"


def test_model_spec_reads_existing_gemini_secondary_env_alias(monkeypatch):
    monkeypatch.delenv("SECOND_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_SECOND_API_KEY", "gemini-secondary-test")

    from kindly_web_search_mcp_server.inference.registry import get_model

    spec = get_model("gemini-3.1-flash-lite@google:second")
    assert spec.api_key == "gemini-secondary-test"


def test_qualified_openai_provider_keys_resolve_secondary_credentials():
    from kindly_web_search_mcp_server.inference.registry import get_model

    cerebras = get_model("gpt-oss-120b@cerebras:second")
    groq = get_model("gpt-oss-20b@groq:second")

    assert (cerebras.provider, cerebras.api_key_env) == (
        "cerebras",
        "SECOND_CEREBRAS_API_KEY",
    )
    assert (groq.provider, groq.api_key_env) == ("groq", "SECOND_GROQ_API_KEY")


def test_default_retry_policy_distinguishes_transient_and_deterministic_errors():
    class HttpError(Exception):
        def __init__(self, status_code: int):
            self.status_code = status_code

    assert _is_retryable_error(HttpError(429))
    assert _is_retryable_error(HttpError(503))
    assert not _is_retryable_error(HttpError(401))
    assert not _is_retryable_error(HttpError(400))
    assert not _is_retryable_error(KeyError("unknown provider"))


def test_qualified_provider_adapter_lookup():
    async def _mock_adapter(spec: ModelSpec, **kwargs) -> LLMGeneration:
        return LLMGeneration(spec=spec, content="")

    register_provider_adapter(
        ProviderAdapter(
            name="test_adapter",
            execute=_mock_adapter,
            capabilities=frozenset(),
        )
    )
    assert get_provider("test_adapter").name == "test_adapter"
    assert get_provider("test_adapter:suffix").name == "test_adapter"
    _PROVIDER_ADAPTERS.pop("test_adapter", None)


def test_openrouter_chat_and_rerank_adapters_are_distinct():
    assert get_provider("openrouter").name == "openai"
    assert get_provider("openrouter_rerank").name == "openrouter_rerank"


def test_cerebras_non_harmony_models_strip_reasoning_directive():
    from kindly_web_search_mcp_server.inference.adapters.openai import (
        _adapt_cerebras_messages,
    )

    messages = [
        {
            "role": "system",
            "content": "Reasoning: low\nKnowledge cutoff: 2024-06\nUse JSON only.",
        },
        {"role": "user", "content": "Rewrite this query."},
    ]
    glm_spec = ModelSpec(
        spec_id="zai-glm-4.7@cerebras",
        provider="cerebras",
        model_id="zai-glm-4.7",
        base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        capabilities=frozenset(),
    )
    gpt_spec = ModelSpec(
        spec_id="gpt-oss-120b@cerebras",
        provider="cerebras",
        model_id="gpt-oss-120b",
        base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        capabilities=frozenset(),
    )

    adapted = _adapt_cerebras_messages(glm_spec, messages)
    assert adapted[0]["content"] == "Knowledge cutoff: 2024-06\nUse JSON only."
    assert adapted[1] is messages[1]
    assert messages[0]["content"].startswith("Reasoning: low")
    assert _adapt_cerebras_messages(gpt_spec, messages) is messages


def test_rankllm_accepts_string_candidate_ids():
    from kindly_web_search_mcp_server.rerank.llm_rerank import _ranked_permutation

    result = SimpleNamespace(candidates=[SimpleNamespace(docid="1"), SimpleNamespace(docid="0")])
    assert [item.index for item in _ranked_permutation(result, 2)] == [1, 0]


@pytest.mark.asyncio
async def test_worker_binds_and_restores_run_context(monkeypatch):
    from kindly_web_search_mcp_server.inference import worker as worker_module
    from kindly_web_search_mcp_server.inference.engine import current_operation, current_run_key
    from kindly_web_search_mcp_server.inference.worker import (
        StructuredLLMRequest,
        build_llm_worker,
    )

    observed: dict[str, str | None] = {}

    async def _fake_execute(chain, **kwargs):
        observed["run_key"] = current_run_key()
        observed["operation"] = current_operation()
        return ExecutionResult(
            spec=chain.primary,
            payload=LLMGeneration(spec=chain.primary, content="{}"),
            elapsed_seconds=0.0,
        )

    monkeypatch.setattr(worker_module, "execute_with_fallback", _fake_execute)
    response = await build_llm_worker().complete_structured(
        StructuredLLMRequest(
            task="rewrite",
            messages=[],
            run_key="run-123",
            operation="rewrite_query",
        )
    )

    assert response.content == "{}"
    assert observed == {"run_key": "run-123", "operation": "rewrite_query"}
    assert current_run_key() is None
    assert current_operation() == "unknown"


# --- Fallback engine tests using mock providers ---


def _pcfg(model_id: str, timeout: float = 30.0) -> ProviderConfig:
    return ProviderConfig(
        model_id=model_id,
        base_url=None,
        api_key_env="TEST_KEY",
        default_timeout=timeout,
    )


async def _mock_primary(spec: ModelSpec, **kwargs) -> LLMGeneration:
    return LLMGeneration(spec=spec, content="success_primary")


async def _mock_fallback(spec: ModelSpec, **kwargs) -> LLMGeneration:
    return LLMGeneration(spec=spec, content="success_fallback")


async def _mock_failing(spec: ModelSpec, **kwargs) -> LLMGeneration:
    raise RuntimeError("provider unavailable")


@pytest.fixture(autouse=True)
def _setup_mocks():
    register_provider_adapter(
        ProviderAdapter(
            name="mock_primary",
            execute=_mock_primary,
            capabilities=frozenset(),
        )
    )
    register_provider_adapter(
        ProviderAdapter(
            name="mock_fallback",
            execute=_mock_fallback,
            capabilities=frozenset(),
        )
    )
    define_model("test-primary", display_name="Test Primary", capabilities=set())
    add_provider("test-primary", "mock_primary", _pcfg("p-model"))
    define_model("test-fallback", display_name="Test Fallback", capabilities=set())
    add_provider("test-fallback", "mock_fallback", _pcfg("f-model"))
    yield
    for key in ("mock_primary", "mock_fallback", "mock_slow", "mock_fatal"):
        _PROVIDER_ADAPTERS.pop(key, None)


@pytest.mark.asyncio
async def test_execute_with_fallback_success():
    chain = ChainSpec("test_chain", ("test-primary@mock_primary", "test-fallback@mock_fallback"))
    res = await execute_with_fallback(chain, "unit_test")
    assert isinstance(res, ExecutionResult)
    assert res.payload.content == "success_primary"
    assert res.spec.provider == "mock_primary"


@pytest.mark.asyncio
async def test_execute_with_fallback_triggers_secondary():
    original = get_provider("mock_primary")
    register_provider_adapter(
        ProviderAdapter(
            name="mock_primary",
            execute=_mock_failing,
            capabilities=frozenset(),
        )
    )
    chain = ChainSpec("test_chain", ("test-primary@mock_primary", "test-fallback@mock_fallback"))
    try:
        res = await execute_with_fallback(chain, "unit_test")
        assert res.payload.content == "success_fallback"
        assert res.spec.provider == "mock_fallback"
    finally:
        register_provider_adapter(original)


@pytest.mark.asyncio
async def test_execute_with_fallback_stops_on_bad_request():
    class BadRequestError(Exception):
        status_code = 400

    async def _mock_bad_request(spec: ModelSpec, **kwargs) -> LLMGeneration:
        raise BadRequestError("invalid request")

    register_provider_adapter(
        ProviderAdapter(
            name="mock_bad_request",
            execute=_mock_bad_request,
            capabilities=frozenset(),
        )
    )
    define_model("test-bad-request", display_name="Test Bad Request", capabilities=set())
    add_provider("test-bad-request", "mock_bad_request", _pcfg("bad-model"))
    chain = ChainSpec(
        "bad_request_chain",
        ("test-bad-request@mock_bad_request", "test-fallback@mock_fallback"),
    )
    try:
        with pytest.raises(BadRequestError):
            await execute_with_fallback(chain, "unit_test")
    finally:
        _PROVIDER_ADAPTERS.pop("mock_bad_request", None)


@pytest.mark.asyncio
async def test_execute_with_fallback_exhaustion():
    register_provider_adapter(
        ProviderAdapter(
            name="mock_primary",
            execute=_mock_failing,
            capabilities=frozenset(),
        )
    )
    register_provider_adapter(
        ProviderAdapter(
            name="mock_fallback",
            execute=_mock_failing,
            capabilities=frozenset(),
        )
    )
    chain = ChainSpec("failing_chain", ("test-primary@mock_primary", "test-fallback@mock_fallback"))
    try:
        with pytest.raises(ChainExhaustedError) as exc_info:
            await execute_with_fallback(chain, "unit_test")
        assert "Chain 'failing_chain' exhausted" in str(exc_info.value)
        assert len(exc_info.value.errors) == 2
    finally:
        register_provider_adapter(
            ProviderAdapter(
                name="mock_primary",
                execute=_mock_primary,
                capabilities=frozenset(),
            )
        )
        register_provider_adapter(
            ProviderAdapter(
                name="mock_fallback",
                execute=_mock_fallback,
                capabilities=frozenset(),
            )
        )


@pytest.mark.asyncio
async def test_execute_with_fallback_timeout():
    async def _mock_slow(spec: ModelSpec, **kwargs) -> LLMGeneration:
        await asyncio.sleep(0.2)
        return LLMGeneration(spec=spec, content="too_late")

    register_provider_adapter(
        ProviderAdapter(
            name="mock_slow",
            execute=_mock_slow,
            capabilities=frozenset(),
        )
    )
    define_model("test-slow", display_name="Test Slow", capabilities=set())
    add_provider("test-slow", "mock_slow", _pcfg("s-model", timeout=0.05))
    chain = ChainSpec("timeout_test", ("test-slow@mock_slow", "test-fallback@mock_fallback"))
    try:
        res = await execute_with_fallback(chain, "unit_test")
        assert res.payload.content == "success_fallback"
        assert res.spec.provider == "mock_fallback"
    finally:
        _PROVIDER_ADAPTERS.pop("mock_slow", None)


@pytest.mark.asyncio
async def test_execute_with_fallback_non_retryable():
    class FatalError(Exception):
        pass

    async def _mock_fatal(spec: ModelSpec, **kwargs) -> LLMGeneration:
        raise FatalError("fatal error")

    register_provider_adapter(
        ProviderAdapter(
            name="mock_fatal",
            execute=_mock_fatal,
            capabilities=frozenset(),
        )
    )
    define_model("test-fatal", display_name="Test Fatal", capabilities=set())
    add_provider("test-fatal", "mock_fatal", _pcfg("x-model"))
    chain = ChainSpec("fatal_test", ("test-fatal@mock_fatal", "test-fallback@mock_fallback"))

    def is_retryable(exc: Exception) -> bool:
        return not isinstance(exc, FatalError)

    try:
        with pytest.raises(FatalError):
            await execute_with_fallback(chain, "unit_test", is_retryable=is_retryable)
    finally:
        _PROVIDER_ADAPTERS.pop("mock_fatal", None)
