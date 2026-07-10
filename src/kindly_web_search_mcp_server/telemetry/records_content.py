"""Telemetry recording helpers for content resolution."""

from __future__ import annotations

import logging
from .metrics import _content_fallback_counter, _content_error_counter
from ..utils.observability import emit_observability_event
from .attributes import (
    CONTENT_EXTRACTION_METHOD,
    CONTENT_FINAL_STAGE,
    CONTENT_STAGE,
    CONTENT_STATUS,
    CONTENT_URL,
    ERROR_TYPE,
    STATUS_SUCCESS,
)
from .metrics import get_content_metrics


def record_content_resolution(
    stage: str,
    url: str,
    success: bool,
    size_bytes: int | None = None,
    duration_seconds: float | None = None,
    word_count: int | None = None,
    extraction_method: str | None = None,
) -> None:
    """Record content resolution stage."""
    resolution_counter, duration_histogram = get_content_metrics()

    status = STATUS_SUCCESS if success else "fallback"
    resolution_counter.add(
        1,
        {
            CONTENT_STAGE: stage,
            CONTENT_FINAL_STAGE: stage,
            CONTENT_STATUS: status,
            CONTENT_EXTRACTION_METHOD: extraction_method or "",
        },
    )

    if duration_seconds is not None:
        duration_histogram.record(
            duration_seconds,
            {
                CONTENT_STAGE: stage,
                CONTENT_STATUS: status,
            },
        )

    emit_observability_event(
        logging.getLogger(__name__),
        "content.stage.resolution",
        stage=stage,
        url=url,
        success=success,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        word_count=word_count,
        extraction_method=extraction_method,
        content_status=status,
    )


def record_content_fallback(stage: str, url: str, from_stage: str | None = None) -> None:
    """Record a fallback to a later extraction stage."""
    counter = _content_fallback_counter
    if counter is None:
        # Ensure initialized
        get_content_metrics()
        counter = _content_fallback_counter
    if counter:
        counter.add(
            1,
            {
                CONTENT_STAGE: stage,
                "content.from_stage": from_stage or "",
                CONTENT_URL: url[:200] if url else "",
            },
        )

    emit_observability_event(
        logging.getLogger(__name__),
        "content.stage.fallback",
        stage=stage,
        url=url,
        from_stage=from_stage,
    )


def record_content_error(stage: str, url: str, error_type: str) -> None:
    """Record a hard error during content resolution."""
    counter = _content_error_counter
    if counter is None:
        get_content_metrics()
        counter = _content_error_counter
    if counter:
        counter.add(
            1,
            {
                CONTENT_STAGE: stage,
                ERROR_TYPE: error_type,
                CONTENT_URL: url[:200] if url else "",
            },
        )

    emit_observability_event(
        logging.getLogger(__name__),
        "content.stage.error",
        level=logging.WARNING,
        stage=stage,
        url=url,
        error_type=error_type,
    )


__all__ = [
    "record_content_error",
    "record_content_fallback",
    "record_content_resolution",
]
