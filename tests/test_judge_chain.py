"""Unit tests for the two-stage judge inference chain (analytics/judges.py).

All network calls are mocked at the stage boundaries (`_call_gemini_stage`
/ `_call_nanogpt_stage`); `time.sleep` is stubbed so exponential-backoff
sequences are asserted without waiting. Covers: primary success, transient
retry + backoff shape, non-retryable immediate failover, total exhaustion,
empty-content failover, response_format rejection salvage, and the retry
classifier.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kindly_web_search_mcp_server.analytics import judges
from kindly_web_search_mcp_server.settings import settings

CTX: list[dict[str, object]] = [
    {"name": "query", "data": "test query"},
    {"name": "research_goal", "data": "test goal"},
    {"name": "intent", "data": "testing"},
    {"name": "understanding_confidence", "data": "0.9"},
    {"name": "rewrites", "data": ""},
]

RF: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {"name": "t", "strict": True, "schema": {}},
}


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(settings, "judge_retry_initial_backoff_seconds", 1.0)
    monkeypatch.setattr(settings, "judge_retry_max_backoff_seconds", 8.0)
    sleeps: list[float] = []
    monkeypatch.setattr(judges.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _wire_stages(monkeypatch, gemini_script, nanogpt_script):
    """Replace stage callables with scripted queues; return call counters."""

    def _make(queue, counter):
        def _stage(*_args, **_kwargs):
            counter["calls"] += 1
            step = queue.pop(0) if queue else "ok-from-stage"
            if isinstance(step, BaseException):
                raise step
            return str(step)

        return _stage

    g_calls, n_calls = {"calls": 0}, {"calls": 0}
    monkeypatch.setattr(judges, "_call_gemini_stage", _make(gemini_script, g_calls))
    monkeypatch.setattr(judges, "_call_nanogpt_stage", _make(nanogpt_script, n_calls))
    return g_calls, n_calls


def _run_chain(monkeypatch, gemini_script, nanogpt_script, rf=RF):
    g_calls, n_calls = _wire_stages(monkeypatch, gemini_script, nanogpt_script)
    raw, duration = judges._judge_chain_call(
        model_name="judge_quality",
        prompt_name="judge_intent_coherence",
        context_columns=CTX,
        response_format=rf,
    )
    return raw, g_calls["calls"], n_calls["calls"], duration


def test_primary_gemini_success_skips_nanogpt(monkeypatch) -> None:
    raw, g, n, _ = _run_chain(monkeypatch, ["gemini-ok"], ["should-not-run"])
    assert raw == "gemini-ok"
    assert (g, n) == (1, 0)


def test_transient_gemini_failures_retry_with_exponential_backoff(
    monkeypatch, fast_backoff
) -> None:
    script = [RuntimeError("503 service unavailable"), RuntimeError("timeout"), "gemini-ok"]
    raw, g, n, _ = _run_chain(monkeypatch, script, [])
    assert raw == "gemini-ok"
    assert g == 3  # 1 initial + judge_stage_max_retries (2)
    assert n == 0
    assert fast_backoff == [1.0, 2.0]


def test_non_retryable_gemini_error_fails_over_immediately(monkeypatch, fast_backoff) -> None:
    err = RuntimeError("402 credits depleted")
    raw, g, n, _ = _run_chain(monkeypatch, [err], ["nanogpt-ok"])
    assert raw == "nanogpt-ok"
    assert (g, n) == (1, 1)
    assert fast_backoff == []  # no backoff sleep before failover


def test_all_stages_exhausted_raises_runtime_error(monkeypatch) -> None:
    boom = RuntimeError("service unavailable")
    with pytest.raises(RuntimeError, match="all judge stages failed"):
        _run_chain(
            monkeypatch,
            [boom, boom, boom],
            [boom, boom, boom],
        )


def test_empty_completion_counts_as_failure_and_fails_over(monkeypatch) -> None:
    raw, g, n, _ = _run_chain(monkeypatch, [""], ["nanogpt-ok"])
    assert raw == "nanogpt-ok"
    assert (g, n) == (1, 1)


# ---------------------------------------------------------------------------
# Stage-2 response_format rejection salvage (function-level, fake OpenAI SDK)
# ---------------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, script):
        self._script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _fake_openai_factory(monkeypatch, completions: _FakeCompletions):
    import openai

    def _factory(**client_kwargs):
        assert client_kwargs["base_url"] == "https://nano-gpt.com/api/subscription/v1"
        assert client_kwargs["max_retries"] == 0
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    monkeypatch.setattr(openai, "OpenAI", _factory)


def _http_exc(status: int, msg: str) -> Exception:
    exc = RuntimeError(msg)
    exc.status_code = status  # type: ignore[attr-defined]
    return exc


def test_nanogpt_response_format_rejection_retries_without_it(monkeypatch) -> None:
    completions = _FakeCompletions(
        [
            _http_exc(400, "response_format json_schema not supported"),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"verdict":"coherent"}'))]
            ),
        ]
    )
    _fake_openai_factory(monkeypatch, completions)
    monkeypatch.setattr(settings, "nano_gpt_api_key", "sentinel")

    out = judges._call_nanogpt_stage("judge_intent_coherence", "prompt-text", RF)

    assert out == '{"verdict":"coherent"}'
    assert len(completions.calls) == 2
    assert "response_format" in completions.calls[0]
    assert "response_format" not in completions.calls[1]


def test_nanogpt_transparently_propagates_other_errors(monkeypatch) -> None:
    completions = _FakeCompletions([_http_exc(500, "boom")])
    _fake_openai_factory(monkeypatch, completions)
    monkeypatch.setattr(settings, "nano_gpt_api_key", "sentinel")

    with pytest.raises(Exception, match="boom"):
        judges._call_nanogpt_stage("judge_intent_coherence", "prompt-text", RF)


def test_nanogpt_requires_api_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "nano_gpt_api_key", "")
    with pytest.raises(RuntimeError, match="NANOGPT_API_KEY"):
        judges._call_nanogpt_stage("judge_intent_coherence", "prompt-text", RF)


# ---------------------------------------------------------------------------
# Retry classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_http_exc(429, "rate limited"), True),
        (_http_exc(503, "unavailable"), True),
        (_http_exc(402, "credits"), False),
        (_http_exc(401, "auth"), False),
        (_http_exc(400, "bad request"), False),
        (RuntimeError("connection reset by peer"), True),
    ]
)
def test_is_retryable_stage_error(exc, expected) -> None:
    assert judges._is_retryable_stage_error(exc) is expected


def test_nanogpt_unrelated_400_propagates(monkeypatch) -> None:
    """A 400 unrelated to response_format must surface, not retry schema-less."""
    completions = _FakeCompletions([_http_exc(400, "invalid_model: unknown model id")])
    _fake_openai_factory(monkeypatch, completions)
    monkeypatch.setattr(settings, "nano_gpt_api_key", "sentinel")

    with pytest.raises(Exception, match="invalid_model"):
        judges._call_nanogpt_stage("judge_intent_coherence", "prompt-text", RF)
    assert len(completions.calls) == 1


def test_parse_result_unwraps_json_array() -> None:
    """Thinking models sometimes wrap the verdict object in a JSON list."""
    raw = '[{"feedback": "x", "verdict": "coherent", "confidence": 3}]'
    assert judges._parse_result(raw) == {"feedback": "x", "verdict": "coherent", "confidence": 3}


def test_nanogpt_salvages_leaked_chain_of_thought(monkeypatch) -> None:
    """Leaked snake_case CoT is cut at the last 'Feedback:' anchor."""
    leaked = (
        "i_will_follow_the_task_description_and_output}Feedback: analysis"
        ' here [RESULT] {"verdict": "coherent", "confidence": 4}'
    )
    completions = _FakeCompletions(
        [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=leaked))])]
    )
    _fake_openai_factory(monkeypatch, completions)
    monkeypatch.setattr(settings, "nano_gpt_api_key", "sentinel")

    out = judges._call_nanogpt_stage("judge_intent_coherence", "prompt-text", None)

    assert out.startswith("Feedback:")
    assert judges._parse_result(out) == {"verdict": "coherent", "confidence": 4}


def test_nanogpt_conformance_pass_recovers_verdict(monkeypatch) -> None:
    """Unparseable stage output triggers exactly one self-extraction call."""
    completions = _FakeCompletions(
        [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="true"))]),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='[RESULT] {"verdict": "coherent", "confidence": 4}'
                        )
                    )
                ]
            ),
        ]
    )
    _fake_openai_factory(monkeypatch, completions)
    monkeypatch.setattr(settings, "nano_gpt_api_key", "sentinel")

    out = judges._call_nanogpt_stage("judge_intent_coherence", "prompt-text", RF)

    assert judges._parse_result(out) == {"verdict": "coherent", "confidence": 4}
    assert len(completions.calls) == 2
