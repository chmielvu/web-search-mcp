"""Generic fallback execution engine across provider specifications."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar

from ..telemetry import create_llm_operation_span, set_span_error, set_span_success
from .catalog import FallbackChainSpec, ModelSpec

T = TypeVar("T")
logger = logging.getLogger(__name__)


class ChainExhaustedError(RuntimeError):
    """Raised when every provider attempt in a chain fails."""

    def __init__(self, chain_name: str, errors: list[tuple[ModelSpec, Exception]]):
        self.chain_name = chain_name
        self.errors = errors
        message = f"Chain '{chain_name}' exhausted after {len(errors)} failure(s): " + ", ".join(
            f"{spec.provider}:{spec.model_id} ({exc})" for spec, exc in errors
        )
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ExecutionResult(Generic[T]):
    spec: ModelSpec
    payload: T
    elapsed_seconds: float


async def execute_with_fallback(
    chain: FallbackChainSpec,
    operation: str,
    handler: Callable[[ModelSpec], Awaitable[T]],
    *,
    is_retryable: Callable[[Exception], bool] = lambda _: True,
) -> ExecutionResult[T]:
    """Execute handler down a fallback chain with telemetry and error capture."""
    errors: list[tuple[ModelSpec, Exception]] = []

    for spec in (chain.primary, *chain.fallbacks):
        t0 = time.perf_counter()
        try:
            with create_llm_operation_span(
                operation,
                system=spec.provider,
                attributes={"llm.model_name": spec.model_id},
            ) as span:
                try:
                    result = await asyncio.wait_for(handler(spec), timeout=spec.default_timeout)
                    set_span_success(span)
                    elapsed = time.perf_counter() - t0
                    return ExecutionResult(spec=spec, payload=result, elapsed_seconds=elapsed)
                except Exception as exc:
                    set_span_error(span, exc)
                    raise
        except Exception as exc:
            if not is_retryable(exc):
                raise
            errors.append((spec, exc))
            logger.warning(
                "Inference attempt failed for chain '%s' using provider '%s' (%s): %s",
                chain.name,
                spec.provider,
                spec.model_id,
                exc,
            )

    raise ChainExhaustedError(chain.name, errors)
