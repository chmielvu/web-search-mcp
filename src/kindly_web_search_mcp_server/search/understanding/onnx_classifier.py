"""HTTP client for the ONNX intent classifier service.

Calls the TinyBERT ONNX INT8 classifier deployed as a Docker service
on the VPS. Returns (intent, confidence) or None on failure.
The classifier is the primary intent resolution path — the LLM-based
query understanding in resolver.py is the fallback for low-confidence
cases or when the classifier service is unavailable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from ...settings import settings
from ..intents import INTENT_ALIASES, normalize_intent

logger = logging.getLogger(__name__)

_VALID_LABELS = frozenset((*INTENT_ALIASES, *INTENT_ALIASES.values()))


@dataclass(frozen=True, slots=True)
class ClassifierPrediction:
    label: str | None = None
    confidence: float | None = None
    scores: dict[str, float] = field(default_factory=dict)
    model: str | None = None
    endpoint: str | None = None
    latency_ms: float | None = None
    http_status: int | None = None
    error_type: str | None = None
    error_message: str | None = None

    @property
    def usable(self) -> bool:
        return self.label is not None and self.confidence is not None and bool(self.scores)


async def classify_intent(query: str) -> ClassifierPrediction:
    """Call the ONNX classifier service.

    Returns a prediction with error metadata when the service is unavailable.
    """
    endpoint = f"{settings.intent_classifier_url}/classify"
    started = time.perf_counter()
    if not settings.intent_classifier_enabled:
        return ClassifierPrediction(
            endpoint=endpoint,
            latency_ms=0.0,
            error_type="disabled",
            error_message="intent classifier disabled",
        )

    try:
        async with httpx.AsyncClient(timeout=settings.intent_classifier_timeout_seconds) as client:
            resp = await client.post(endpoint, json={"text": query})
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("classifier returned a non-object payload")
            score_map: dict[str, float] = {}
            raw_scores = data.get("scores") or []
            if not isinstance(raw_scores, list):
                raise ValueError("classifier scores must be a list")
            for item in raw_scores:
                if not isinstance(item, dict):
                    continue
                raw_label = item.get("intent", item.get("label"))
                raw_score = item.get("score")
                if not isinstance(raw_label, str) or raw_label not in _VALID_LABELS:
                    continue
                if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float, str)):
                    continue
                try:
                    score = float(raw_score)
                except (TypeError, ValueError):
                    continue
                if 0.0 <= score <= 1.0:
                    score_map[normalize_intent(raw_label)] = score
            if not score_map:
                raise ValueError("classifier returned no valid intent scores")
            raw_label = data.get("intent")
            label = normalize_intent(str(raw_label)) if isinstance(raw_label, str) else None
            if label not in score_map:
                label = max(score_map, key=lambda intent: score_map[intent])
            return ClassifierPrediction(
                label=label,
                confidence=score_map[label],
                scores=score_map,
                model=str(data.get("model") or data.get("model_version") or "") or None,
                endpoint=endpoint,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                http_status=resp.status_code,
            )
    except httpx.HTTPStatusError as exc:
        logger.debug("ONNX classifier call failed: %s", exc)
        return ClassifierPrediction(
            endpoint=endpoint,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            http_status=exc.response.status_code,
            error_type="http_status",
            error_message=str(exc)[:500],
        )
    except Exception as exc:
        logger.debug("ONNX classifier call failed: %s", exc)
        return ClassifierPrediction(
            endpoint=endpoint,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
        )
