"""Unit tests for the inference catalog and fallback engine."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.inference import (
    ChainExhaustedError,
    ChainSpec,
    ExecutionResult,
    ModelSpec,
    execute_with_fallback,
    get_chain,
)
from kindly_web_search_mcp_server.inference import is_retryable_error
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
    assert chain.primary.provider == "groq"
    assert len(chain.fallbacks) == 3
    assert [spec.api_key_env for spec in chain.models] == [
        "GROQ_API_KEY",
        "SECOND_GROQ_API_KEY",
        "HF_TOKEN",
        "AI_GATEWAY_API_KEY",
    ]
    assert [spec.model_id for spec in chain.models] == [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-120b:nscale",
        "openai/gpt-oss-20b",
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

    groq = get_model("gpt-oss-20b@groq:second")
    worker = get_model("gpt-oss-120b@groq:second")

    assert (groq.provider, groq.api_key_env) == ("groq", "SECOND_GROQ_API_KEY")
    assert (worker.provider, worker.api_key_env) == ("groq", "SECOND_GROQ_API_KEY")


def test_default_retry_policy_distinguishes_transient_and_deterministic_errors():
    class HttpError(Exception):
        def __init__(self, status_code: int):
            self.status_code = status_code

    assert is_retryable_error(HttpError(429))
    assert is_retryable_error(HttpError(503))
    assert is_retryable_error(HttpError(402))
    assert is_retryable_error(HttpError(401))
    assert is_retryable_error(HttpError(403))
    assert is_retryable_error(HttpError(400))
    assert is_retryable_error(HttpError(404))
    assert not is_retryable_error(HttpError(422))
    assert not is_retryable_error(KeyError("unknown provider"))


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


@pytest.mark.asyncio
async def test_voyage_adapter_uses_voyage_request_contract(monkeypatch):
    import httpx
    from kindly_web_search_mcp_server.inference.adapters.voyage import execute_voyage_rerank

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 1, "relevance_score": 0.9}]}

    class _FakeClient:
        def __init__(self):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return _FakeResponse()

    fake_client = _FakeClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: fake_client)
    spec = get_chain("cross_encoder_rerank").models[-1]

    generation = await execute_voyage_rerank(
        spec,
        query="authoritative docs",
        documents=["doc a", "doc b"],
        top_n=2,
    )

    url, request = fake_client.calls[0]
    assert url == "https://api.voyageai.com/v1/rerank"
    assert request["json"]["top_k"] == 2
    assert "top_n" not in request["json"]
    assert request["json"]["documents"] == ["doc a", "doc b"]
    assert generation.content == '[{"index": 1, "relevance_score": 0.9}]'


def test_openrouter_chat_and_rerank_adapters_are_distinct():
    assert get_provider("openrouter").name == "openai"
    assert get_provider("openrouter_rerank").name == "openrouter_rerank"


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
async def test_rerank_provider_fallback_validates_each_attempt() -> None:
    from kindly_web_search_mcp_server.rerank import providers as rerank_providers

    calls: list[str] = []

    async def invalid_primary(spec: ModelSpec, **kwargs) -> LLMGeneration:
        calls.append("primary")
        return LLMGeneration(
            spec=spec,
            content=json.dumps(
                [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.8},
                ]
            ),
        )

    async def valid_fallback(spec: ModelSpec, **kwargs) -> LLMGeneration:
        calls.append("fallback")
        return LLMGeneration(
            spec=spec,
            content=json.dumps(
                [
                    {"index": 1, "relevance_score": 0.95},
                    {"index": 0, "relevance_score": 0.4},
                ]
            ),
        )

    register_provider_adapter(
        ProviderAdapter(
            name="mock_rerank_invalid_primary",
            execute=invalid_primary,
            capabilities=frozenset(),
        )
    )
    register_provider_adapter(
        ProviderAdapter(
            name="mock_rerank_valid_fallback",
            execute=valid_fallback,
            capabilities=frozenset(),
        )
    )
    define_model("test-rerank-invalid-primary", display_name="Invalid Rerank", capabilities=set())
    add_provider(
        "test-rerank-invalid-primary",
        "mock_rerank_invalid_primary",
        _pcfg("invalid-rerank-model"),
    )
    define_model("test-rerank-valid-fallback", display_name="Valid Rerank", capabilities=set())
    add_provider(
        "test-rerank-valid-fallback",
        "mock_rerank_valid_fallback",
        _pcfg("valid-rerank-model"),
    )
    chain = ChainSpec(
        "rerank_validation_chain",
        (
            "test-rerank-invalid-primary@mock_rerank_invalid_primary",
            "test-rerank-valid-fallback@mock_rerank_valid_fallback",
        ),
    )
    candidates = [
        WebSearchResult(title="A", link="https://example.com/a", snippet="A"),
        WebSearchResult(title="B", link="https://example.com/b", snippet="B"),
    ]
    try:
        with patch.object(rerank_providers, "get_chain", return_value=chain):
            outcome = await rerank_providers.rerank_with_provider_fallback("query", candidates)
    finally:
        _PROVIDER_ADAPTERS.pop("mock_rerank_invalid_primary", None)
        _PROVIDER_ADAPTERS.pop("mock_rerank_valid_fallback", None)

    assert calls == ["primary", "fallback"]
    assert outcome.error is None
    assert [(item.index, item.relevance_score) for item in outcome.ranked] == [
        (1, 0.95),
        (0, 0.4),
    ]
    assert outcome.provider_id == "mock_rerank_valid_fallback"


@pytest.mark.asyncio
async def test_rerank_provider_fallback_fails_open_when_all_responses_invalid() -> None:
    from kindly_web_search_mcp_server.rerank import providers as rerank_providers
    from kindly_web_search_mcp_server.rerank.providers import RerankResponseError

    calls: list[str] = []

    async def invalid(spec: ModelSpec, **kwargs) -> LLMGeneration:
        calls.append(spec.provider)
        return LLMGeneration(
            spec=spec,
            content=json.dumps([{"index": 4, "relevance_score": 0.5}]),
        )

    register_provider_adapter(
        ProviderAdapter(
            name="mock_rerank_invalid_a",
            execute=invalid,
            capabilities=frozenset(),
        )
    )
    register_provider_adapter(
        ProviderAdapter(
            name="mock_rerank_invalid_b",
            execute=invalid,
            capabilities=frozenset(),
        )
    )
    define_model("test-rerank-invalid-a", display_name="Invalid A", capabilities=set())
    add_provider("test-rerank-invalid-a", "mock_rerank_invalid_a", _pcfg("invalid-a"))
    define_model("test-rerank-invalid-b", display_name="Invalid B", capabilities=set())
    add_provider("test-rerank-invalid-b", "mock_rerank_invalid_b", _pcfg("invalid-b"))
    chain = ChainSpec(
        "all_invalid_rerank_chain",
        (
            "test-rerank-invalid-a@mock_rerank_invalid_a",
            "test-rerank-invalid-b@mock_rerank_invalid_b",
        ),
    )
    candidates = [
        WebSearchResult(title="A", link="https://example.com/a", snippet="A"),
        WebSearchResult(title="B", link="https://example.com/b", snippet="B"),
    ]
    try:
        with patch.object(rerank_providers, "get_chain", return_value=chain):
            outcome = await rerank_providers.rerank_with_provider_fallback("query", candidates)
    finally:
        _PROVIDER_ADAPTERS.pop("mock_rerank_invalid_a", None)
        _PROVIDER_ADAPTERS.pop("mock_rerank_invalid_b", None)

    assert calls == ["mock_rerank_invalid_a", "mock_rerank_invalid_b"]
    assert outcome.ranked == []
    assert isinstance(outcome.error, RerankResponseError)


@pytest.mark.asyncio
async def test_execute_with_fallback_does_not_chain_on_plain_value_error() -> None:
    calls = 0

    async def invalid_handler(spec: ModelSpec) -> LLMGeneration:
        nonlocal calls
        calls += 1
        raise ValueError("invalid local request")

    chain = ChainSpec(
        "plain_value_error_chain", ("test-primary@mock_primary", "test-fallback@mock_fallback")
    )
    with pytest.raises(ValueError, match="invalid local request"):
        await execute_with_fallback(chain, "unit_test", handler=invalid_handler)
    assert calls == 1


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
@pytest.mark.parametrize("status_code", [400, 401, 402, 403, 404])
async def test_execute_with_fallback_advances_on_provider_http_errors(status_code: int):
    class ProviderHttpError(Exception):
        def __init__(self) -> None:
            super().__init__(f"http {status_code}")
            self.status_code = status_code

    async def _mock_http(spec: ModelSpec, **kwargs) -> LLMGeneration:
        raise ProviderHttpError()

    adapter_name = f"mock_http_{status_code}"
    model_id = f"test-http-{status_code}"
    register_provider_adapter(
        ProviderAdapter(
            name=adapter_name,
            execute=_mock_http,
            capabilities=frozenset(),
        )
    )
    define_model(model_id, display_name=f"Test HTTP {status_code}", capabilities=set())
    add_provider(model_id, adapter_name, _pcfg("http-model"))
    chain = ChainSpec(
        f"http_{status_code}_chain",
        (f"{model_id}@{adapter_name}", "test-fallback@mock_fallback"),
    )
    try:
        res = await execute_with_fallback(chain, "unit_test")
        assert res.payload.content == "success_fallback"
        assert res.spec.provider == "mock_fallback"
    finally:
        _PROVIDER_ADAPTERS.pop(adapter_name, None)


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
