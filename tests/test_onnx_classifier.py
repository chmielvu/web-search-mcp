from __future__ import annotations

from types import SimpleNamespace

import pytest

from kindly_web_search_mcp_server.search.understanding import onnx_classifier
from kindly_web_search_mcp_server.settings import settings


class _Client:
    def __init__(self, response: object) -> None:
        self.response = response

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, endpoint: str, *, json: dict[str, str]) -> object:
        return self.response


@pytest.mark.asyncio
async def test_classifier_preserves_score_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "intent": "code",
            "model_version": "tinybert-v2",
            "scores": [
                {"intent": "code", "score": 0.81},
                {"intent": "general", "score": 0.19},
            ],
        },
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr(onnx_classifier.httpx, "AsyncClient", lambda **kwargs: _Client(response))
    monkeypatch.setattr(settings, "intent_classifier_enabled", True)

    prediction = await onnx_classifier.classify_intent("python timeout")

    assert prediction.usable
    assert prediction.label == "ai_coding_and_infrastructure"
    assert prediction.confidence == 0.81
    assert prediction.scores == {"ai_coding_and_infrastructure": 0.81, "general": 0.19}
    assert prediction.model == "tinybert-v2"
    assert prediction.http_status == 200


@pytest.mark.asyncio
async def test_classifier_malformed_scores_are_auditable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"intent": "unknown", "scores": [{"intent": "unknown", "score": 2.0}]},
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr(onnx_classifier.httpx, "AsyncClient", lambda **kwargs: _Client(response))
    monkeypatch.setattr(settings, "intent_classifier_enabled", True)

    prediction = await onnx_classifier.classify_intent("bad payload")

    assert not prediction.usable
    assert prediction.error_type == "ValueError"
    assert "no valid intent scores" in (prediction.error_message or "")
