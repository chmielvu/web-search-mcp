"""Hosted GLiNER2 gateway for query and optional content extraction.

The application never imports ``gliner2`` or ``torch``. Inference is performed
by the unified ML service exposed through ``INTENT_CLASSIFIER_URL``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..settings import settings
from ..utils.observability import emit_observability_event
from .chunk import chunk_text
from .default_schema import DEFAULT_CONTENT_LABELS, DEFAULT_QUERY_LABELS, DEFAULT_QUERY_RELATIONS
from .models import EntitySpan

logger = logging.getLogger(__name__)

_gliner_client: GLiNER2Client | None = None
_SERVICE_MAX_TEXT_CHARS = 4000
_CONTENT_CHUNK_OVERLAP = 200


def is_entity_extraction_enabled() -> bool:
    """Return true only when optional content extraction is explicitly enabled."""
    raw = getattr(settings, "entity_extraction_enabled", False)
    env_val = os.environ.get("ENTITY_EXTRACTION_ENABLED", "").lower()
    if env_val in ("true", "1", "yes"):
        return True
    if env_val in ("false", "0", "no"):
        return False
    return bool(raw)


@dataclass(frozen=True, slots=True)
class GatewayAnalysis:
    """Normalized query result plus transport metadata for observability."""

    understanding: Any
    model_version: str
    latency_ms: float
    fallback: bool = False
    error_reason: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryFeatureAnalysis:
    """Lightweight classifier and grounded entities for code-query planning."""

    intent: str | None
    confidence: float
    entities: tuple[EntitySpan, ...]
    model_version: str
    latency_ms: float
    fallback: bool = False
    error_reason: str | None = None
    warnings: tuple[str, ...] = ()


class GLiNER2Client:
    """Asynchronous HTTP client for the VPS unified GLiNER2 service."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self._base_url = (base_url or self._resolve_base_url()).rstrip("/")
        self._timeout = timeout if timeout is not None else self._resolve_timeout()
        self._model_name = os.environ.get(
            "GLINER_MODEL", getattr(settings, "gliner_model", "fastino/gliner2-multi-v1")
        )

    @staticmethod
    def _resolve_base_url() -> str:
        return getattr(settings, "intent_classifier_url", "http://127.0.0.1:8000")

    @staticmethod
    def _resolve_timeout() -> float:
        return float(
            getattr(
                settings,
                "intent_classifier_timeout_seconds",
                getattr(settings, "query_classifier_timeout_seconds", 10.0),
            )
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    async def _post(
        self, path: str, payload: dict[str, Any], *, operation: str
    ) -> tuple[dict[str, Any], float]:
        endpoint = f"{self._base_url}/{path.lstrip('/')}"
        started = time.perf_counter()
        emit_observability_event(
            logger,
            "entity.gateway.request",
            operation=operation,
            endpoint=endpoint,
            model=self._model_name,
            text_len=len(str(payload.get("text") or "")),
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError("GLiNER2 service returned a non-object payload")
        latency_ms = (time.perf_counter() - started) * 1000.0
        emit_observability_event(
            logger,
            "entity.gateway.response",
            operation=operation,
            endpoint=endpoint,
            model=str(data.get("model_version") or data.get("model") or self._model_name),
            latency_ms=latency_ms,
            http_status=response.status_code,
        )
        return data, latency_ms

    @staticmethod
    def _fallback_result(reason: str, *, model: str, latency_ms: float = 0.0) -> GatewayAnalysis:
        from ..search.understanding.models import QueryUnderstandingResult

        return GatewayAnalysis(
            understanding=QueryUnderstandingResult(
                intent="general",
                confidence=0.0,
                rationale=reason,
                entities=[],
                relations=[],
                preserved_terms=[],
                compared_entities=[],
                domain_hints=[],
                time_sensitivity="none",
                should_decompose=False,
            ),
            model_version=model,
            latency_ms=latency_ms,
            fallback=True,
            error_reason=reason,
        )

    async def analyze_query(self, text: str) -> GatewayAnalysis:
        """Issue one combined query-understanding call to the VPS service."""
        from ..search.understanding.adapter import (
            QueryUnderstandingContractError,
            normalize_query_understanding_response,
        )

        normalized_text = text.strip()
        if not normalized_text:
            return self._fallback_result("gliner2-empty-query", model=self._model_name)
        if not getattr(settings, "intent_classifier_enabled", True):
            emit_observability_event(
                logger,
                "search.query_understanding.fallback",
                reason="disabled",
                provider="gliner2",
                model=self._model_name,
            )
            return self._fallback_result("gliner2-disabled", model=self._model_name)

        threshold = float(getattr(settings, "intent_classifier_confidence_threshold", 0.5))
        payload = {
            "text": normalized_text,
            "entity_labels": DEFAULT_QUERY_LABELS,
            "relation_labels": DEFAULT_QUERY_RELATIONS,
            "entity_threshold": float(getattr(settings, "gliner_threshold", 0.5)),
            "include_confidence": True,
            "include_spans": True,
        }
        started = time.perf_counter()
        try:
            data, request_latency_ms = await self._post(
                "/v2/query-understanding", payload, operation="query_understanding"
            )
            normalized = normalize_query_understanding_response(
                data,
                normalized_text,
                confidence_threshold=threshold,
            )
            warnings = normalized.warnings
            for warning in warnings:
                emit_observability_event(
                    logger,
                    "search.query_understanding.contract_warning",
                    warning=warning,
                    model=normalized.model_version,
                )
            return GatewayAnalysis(
                understanding=normalized.understanding,
                model_version=normalized.model_version,
                latency_ms=normalized.latency_ms or request_latency_ms,
                warnings=warnings,
            )
        except httpx.HTTPStatusError as exc:
            reason = f"gliner2-http-{exc.response.status_code}"
        except httpx.TimeoutException:
            reason = "gliner2-timeout"
        except (httpx.RequestError, OSError):
            reason = "gliner2-unavailable"
        except (ValueError, TypeError, QueryUnderstandingContractError):
            reason = "gliner2-invalid-contract"
        except Exception as exc:  # pragma: no cover - final safety boundary
            logger.warning("GLiNER2 query gateway failed: %s", exc)
            reason = "gliner2-error"

        latency_ms = (time.perf_counter() - started) * 1000.0
        emit_observability_event(
            logger,
            "search.query_understanding.fallback",
            reason=reason,
            provider="gliner2",
            model=self._model_name,
            latency_ms=latency_ms,
            fallback=True,
        )
        return self._fallback_result(reason, model=self._model_name, latency_ms=latency_ms)

    async def analyze_query_features(self, text: str) -> QueryFeatureAnalysis:
        """Use the deployed classifier and NER endpoints without relation extraction."""

        from ..search.understanding.adapter import normalize_content_entities

        normalized_text = text.strip()
        if not normalized_text:
            return QueryFeatureAnalysis(
                intent=None,
                confidence=0.0,
                entities=(),
                model_version=self._model_name,
                latency_ms=0.0,
                fallback=True,
                error_reason="gliner2-empty-query",
            )
        started = time.perf_counter()
        classify_result, ner_result = await asyncio.gather(
            self._post(
                "/classify",
                {"text": normalized_text},
                operation="code_query_classification",
            ),
            self._post(
                "/ner",
                {"text": normalized_text, "labels": list(DEFAULT_QUERY_LABELS)},
                operation="code_query_entities",
            ),
            return_exceptions=True,
        )
        warnings: list[str] = []
        intent: str | None = None
        confidence = 0.0
        entities: list[EntitySpan] = []
        if isinstance(classify_result, BaseException):
            warnings.append(f"classifier-{type(classify_result).__name__}")
        else:
            classify_payload, _ = classify_result
            raw_intent = classify_payload.get("intent")
            if isinstance(raw_intent, str) and raw_intent.strip():
                intent = raw_intent.strip()
            scores = classify_payload.get("scores")
            if isinstance(scores, list):
                confidence = max(
                    (
                        float(item.get("score"))
                        for item in scores
                        if isinstance(item, dict)
                        and item.get("label") == intent
                        and isinstance(item.get("score"), (int, float))
                    ),
                    default=0.0,
                )
        if isinstance(ner_result, BaseException):
            warnings.append(f"ner-{type(ner_result).__name__}")
        else:
            ner_payload, _ = ner_result
            entities = normalize_content_entities(ner_payload, normalized_text)
        fallback = intent is None and not entities
        return QueryFeatureAnalysis(
            intent=intent,
            confidence=max(0.0, min(1.0, confidence)),
            entities=tuple(entities),
            model_version=self._model_name,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            fallback=fallback,
            error_reason="gliner2-query-features-unavailable" if fallback else None,
            warnings=tuple(warnings),
        )

    async def extract_transcript_chunk(
        self,
        text: str,
        *,
        entities: dict[str, str] | list[str],
        structures: dict[str, Any],
        relations: dict[str, str] | None = None,
        threshold: float | None = None,
    ) -> tuple[dict[str, Any], float]:
        """Run always-on transcript extraction for one offset-preserving chunk.

        Unlike general content extraction, this method is intentionally not
        gated by ``ENTITY_EXTRACTION_ENABLED``. YouTube transcript analysis is
        a product contract and must use the live VPS GLiNER2 service whenever
        a transcript is fetched; failures are handled by the caller as partial
        analysis rather than transcript failure.
        """
        if not text.strip():
            return {}, 0.0
        payload: dict[str, Any] = {
            "text": text,
            "entities": entities,
            "structures": structures,
            "threshold": float(
                threshold if threshold is not None else getattr(settings, "gliner_threshold", 0.5)
            ),
            "include_confidence": True,
            "include_spans": True,
        }
        if relations:
            payload["relations"] = relations
        return await self._post("/extract", payload, operation="youtube_transcript_extraction")

    async def extract_entities(
        self,
        text: str,
        labels: dict[str, str] | list[str] | None = None,
        threshold: float | None = None,
    ) -> list[EntitySpan]:
        """Extract content entities through the VPS gateway when enabled."""
        from ..search.understanding.adapter import normalize_content_entities

        if not text or not text.strip():
            return []
        if not is_entity_extraction_enabled():
            emit_observability_event(
                logger,
                "entity.extraction.skipped",
                reason="disabled",
                operation="content_extraction",
                text_len=len(text),
            )
            return []

        entity_labels = labels or DEFAULT_CONTENT_LABELS
        entity_request_labels: dict[str, str] | list[str] = entity_labels
        service_threshold = float(
            threshold if threshold is not None else getattr(settings, "gliner_threshold", 0.5)
        )
        all_entities: list[EntitySpan] = []
        chunks = (
            [(0, text)]
            if len(text) <= _SERVICE_MAX_TEXT_CHARS
            else chunk_text(
                text,
                chunk_size=_SERVICE_MAX_TEXT_CHARS - 200,
                overlap=_CONTENT_CHUNK_OVERLAP,
            )
        )
        try:
            for offset, chunk in chunks:
                payload = {
                    "text": chunk,
                    "entities": entity_request_labels,
                    "threshold": service_threshold,
                    "include_confidence": True,
                    "include_spans": True,
                }
                data, _ = await self._post("/extract", payload, operation="content_extraction")
                entities = normalize_content_entities(data, chunk)
                all_entities.extend(
                    entity.model_copy(
                        update={
                            "start": entity.start + offset if entity.start is not None else None,
                            "end": entity.end + offset if entity.end is not None else None,
                        }
                    )
                    for entity in entities
                )
            from .postprocess import postprocess_entities

            result = postprocess_entities(all_entities)
            return result
        except httpx.HTTPStatusError as exc:
            reason = f"http-{exc.response.status_code}"
        except httpx.TimeoutException:
            reason = "timeout"
        except (httpx.RequestError, OSError):
            reason = "unavailable"
        except Exception as exc:
            logger.warning("GLiNER2 content extraction failed: %s", exc)
            reason = "invalid-response"
        emit_observability_event(
            logger,
            "entity.extraction.error",
            operation="content_extraction",
            model=self._model_name,
            error=reason,
            failure_mode="gateway_failed",
            retryable=True,
            component="gliner2_gateway",
        )
        return []


def get_gliner_client() -> GLiNER2Client:
    """Return the process-wide hosted GLiNER2 gateway singleton."""
    global _gliner_client
    if _gliner_client is None:
        _gliner_client = GLiNER2Client()
    return _gliner_client
