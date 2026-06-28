"""HTTP client for the ONNX intent classifier service.

Calls the TinyBERT ONNX INT8 classifier deployed as a Docker service
on the VPS. Returns (intent, confidence) or None on failure.
The classifier is the primary intent resolution path — the LLM-based
query understanding in resolver.py is the fallback for low-confidence
cases or when the classifier service is unavailable.
"""

from __future__ import annotations

import logging

import httpx

from ...settings import settings

logger = logging.getLogger(__name__)


async def classify_intent(query: str) -> tuple[str, float] | None:
    """Call the ONNX classifier service.

    Returns (label, confidence) or None if the service is unavailable.
    """
    if not settings.intent_classifier_enabled:
        return None

    try:
        async with httpx.AsyncClient(
            timeout=settings.intent_classifier_timeout_seconds
        ) as client:
            resp = await client.post(
                f"{settings.intent_classifier_url}/classify",
                json={"text": query},
            )
            resp.raise_for_status()
            data = resp.json()
            label = data["intent"]
            scores = data.get("scores", [])
            confidence = scores[0]["score"] if scores else 0.0
            return label, confidence
    except Exception as exc:
        logger.debug("ONNX classifier call failed: %s", exc)
        return None