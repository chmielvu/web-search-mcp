"""Lazy GLiNER2 entity extraction client.

- Optional extra: pip install ...[entity-extraction]
- Controlled by KINDLY_ENTITY_EXTRACTION_ENABLED (default false)
- Never imported at package load time.
- All inference via asyncio.to_thread (CPU bound, non-blocking).
- Explicit disabled state + error events on failure (no silent degradation when enabled).
- Uses emit_observability_event for entity.* events.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..settings import settings
from ..utils.observability import emit_observability_event
from .models import EntitySpan

logger = logging.getLogger(__name__)

_gliner_client: GLiNER2Client | None = None


def is_entity_extraction_enabled() -> bool:
    """Return true only when explicitly enabled via env (KINDLY_ENTITY_EXTRACTION_ENABLED=true)."""
    # Read live from env to support monkeypatch in tests; fall back to settings.
    raw = (settings.entity_extraction_enabled if hasattr(settings, "entity_extraction_enabled") else False)
    # Re-evaluate from env for test dynamism (settings may be snapshot)
    import os

    env_val = os.environ.get("KINDLY_ENTITY_EXTRACTION_ENABLED", "").lower()
    if env_val in ("true", "1", "yes"):
        return True
    if env_val in ("false", "0", "no"):
        return False
    return bool(raw)


class GLiNER2Client:
    """Lazy wrapper around gliner2.GLiNER2.

    Model is loaded on first extract call and cached.
    """

    def __init__(
        self,
        model: str | None = None,
        threshold: float | None = None,
    ) -> None:
        self._model: Any = None
        self._model_name: str = model or self._resolve_model_name()
        self._default_threshold: float = (
            threshold if threshold is not None else self._resolve_threshold()
        )
        self._load_lock = asyncio.Lock()

    def _resolve_model_name(self) -> str:
        import os

        return (
            os.environ.get("KINDLY_GLINER_MODEL")
            or getattr(settings, "gliner_model", "fastino/gliner2-base-v1")
        )

    def _resolve_threshold(self) -> float:
        import os

        raw = os.environ.get("KINDLY_GLINER_THRESHOLD")
        if raw is not None:
            try:
                return float(raw)
            except ValueError:
                pass
        return getattr(settings, "gliner_threshold", 0.5)

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model

        if not is_entity_extraction_enabled():
            return None

        async with self._load_lock:
            if self._model is not None:
                return self._model

            def _load():
                # Import is intentionally inside the thread function so that
                # merely importing this module (or the entity package) never
                # pulls in torch/gliner2 unless we are about to use it.
                try:
                    from gliner2 import GLiNER2  # type: ignore
                except Exception as exc:  # pragma: no cover - exercised via mocks + error path
                    raise RuntimeError(f"gliner2 package not available: {exc}") from exc

                emit_observability_event(
                    logger,
                    "entity.model_loading",
                    model=self._model_name,
                )
                return GLiNER2.from_pretrained(self._model_name)

            try:
                self._model = await asyncio.to_thread(_load)
                emit_observability_event(
                    logger,
                    "entity.model_loaded",
                    model=self._model_name,
                )
            except Exception as exc:
                emit_observability_event(
                    logger,
                    "entity.extraction.error",
                    model=self._model_name,
                    error=str(exc)[:500],
                    failure_mode="model_load_failed",
                    retryable=False,
                    component="gliner2_client",
                )
                logger.warning("GLiNER2 model load failed: %s", exc)
                # leave _model as None; callers see disabled behavior
                self._model = None
                # re-raise only in tests that want strict; production path swallows to []
            return self._model

    async def extract_entities(
        self,
        text: str,
        labels: dict[str, str] | list[str] | None = None,
        threshold: float | None = None,
    ) -> list[EntitySpan]:
        """Extract entities. Returns [] when disabled or on any error."""
        if not text or not text.strip():
            return []

        if not is_entity_extraction_enabled():
            emit_observability_event(
                logger,
                "entity.extraction.skipped",
                reason="disabled",
                text_len=len(text),
            )
            return []

        model = await self._ensure_model()
        if model is None:
            return []

        eff_threshold = threshold if threshold is not None else self._resolve_threshold()
        # labels default to query schema for short inputs; caller may override
        if labels is None:
            from .default_schema import DEFAULT_QUERY_LABELS

            labels = DEFAULT_QUERY_LABELS

        def _infer():
            # GLiNER2 signature (from research): extract_entities(text, labels, threshold=..., flat_ner=..., ...)
            try:
                raw = model.extract_entities(
                    text,
                    labels,
                    threshold=eff_threshold,
                    # include confidence/spans by default in GLiNER2
                )
            except TypeError:
                # fallback for slightly different signatures seen in early releases
                raw = model.extract_entities(text, labels)
            return raw or []

        try:
            raw_results = await asyncio.to_thread(_infer)
        except Exception as exc:
            emit_observability_event(
                logger,
                "entity.extraction.error",
                model=self._model_name,
                error=str(exc)[:500],
                failure_mode="inference_failed",
                retryable=True,
                component="gliner2_client",
            )
            logger.warning("GLiNER2 inference failed: %s", exc)
            return []

        # Normalize whatever GLiNER2 returns (list of dicts or objects) into EntitySpan
        normalized: list[EntitySpan] = []
        for item in raw_results:
            if isinstance(item, dict):
                norm = EntitySpan(
                    text=str(item.get("text") or item.get("entity") or ""),
                    label=str(item.get("label") or item.get("entity_type") or "entity"),
                    start=item.get("start"),
                    end=item.get("end"),
                    confidence=item.get("score") or item.get("confidence"),
                )
            else:
                # object with attrs
                norm = EntitySpan(
                    text=getattr(item, "text", str(item)),
                    label=getattr(item, "label", "entity"),
                    start=getattr(item, "start", None),
                    end=getattr(item, "end", None),
                    confidence=getattr(item, "score", None) or getattr(item, "confidence", None),
                )
            if norm.text:
                normalized.append(norm)

        emit_observability_event(
            logger,
            "entity.extracted",
            model=self._model_name,
            count=len(normalized),
            labels_used=list(labels.keys()) if isinstance(labels, dict) else (labels or []),
            threshold=eff_threshold,
        )

        # Post-process is the caller's responsibility after offset correction.
        # Here we just return normalized spans from this (chunk) call.
        return normalized


def get_gliner_client() -> GLiNER2Client:
    """Return the process-wide lazy singleton client."""
    global _gliner_client
    if _gliner_client is None:
        _gliner_client = GLiNER2Client()
    return _gliner_client
