"""Shared cache observability helpers."""

from __future__ import annotations

import logging
from typing import Any

from ..utils.observability import emit_observability_event


def emit_cache_lookup_event(
    logger: logging.Logger,
    cache_type: str,
    lookup_status: str,
    *,
    level: int = logging.DEBUG,
    **fields: Any,
) -> None:
    emit_observability_event(
        logger,
        "search.cache.lookup",
        level=level,
        cache_type=cache_type,
        lookup_status=lookup_status,
        **fields,
    )


def emit_cache_store_event(
    logger: logging.Logger,
    cache_type: str,
    store_status: str,
    *,
    level: int = logging.DEBUG,
    **fields: Any,
) -> None:
    emit_observability_event(
        logger,
        "search.cache.store",
        level=level,
        cache_type=cache_type,
        store_status=store_status,
        **fields,
    )
