"""Generic fallback execution engine across provider specifications."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import (
    APIConnectionError,
    APITimeoutError,
    ConflictError,
    InternalServerError,
    RateLimitError,
    UnprocessableEntityError,
)

from .chain import ChainSpec
from .registry import get_provider
from .types import ModelSpec

T = TypeVar("T")

_run_key_ctx: ContextVar[str | None] = ContextVar("kindly_run_key", default=None)
_operation_ctx: ContextVar[str] = ContextVar("kindly_operation", default="unknown")


def bind_run_context(run_key: str | None, operation: str) -> tuple[Token[str | None], Token[str]]:
    return _run_key_ctx.set(run_key), _operation_ctx.set(operation)


def reset_run_context(token: tuple[Token[str | None], Token[str]]) -> None:
    rk_token, op_token = token
    _run_key_ctx.reset(rk_token)
    _operation_ctx.reset(op_token)


def current_run_key() -> str | None:
    return _run_key_ctx.get()


def current_operation() -> str:
    return _operation_ctx.get()


logger = logging.getLogger(__name__)


def is_retryable_error(exc: Exception) -> bool:
    """Return whether a provider failure should advance the fallback chain.

    Provider HTTP failures (400/401/402/403/404, rate limits, timeouts,
    conflicts, server errors) are provider-local and advance the chain.
    Request-validation (422) and local configuration errors are deterministic
    and should surface instead of making every fallback provider repeat the
    same invalid request.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, (KeyError, TypeError, ValueError)):
        return False

    openai_retryable = (
        APIConnectionError,
        APITimeoutError,
        ConflictError,
        InternalServerError,
        RateLimitError,
    )
    openai_non_retryable = (UnprocessableEntityError,)

    if isinstance(exc, openai_retryable):
        return True
    if isinstance(exc, openai_non_retryable):
        return False

    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code in {400, 401, 402, 403, 404, 408, 409, 429} or status_code >= 500

    error_name = type(exc).__name__.casefold()
    return not any(marker in error_name for marker in ("validation",))


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
class ExecutionResult[T]:
    spec: ModelSpec
    payload: T
    elapsed_seconds: float


async def execute_with_fallback(
    chain: ChainSpec,
    operation: str,
    handler: Callable[[ModelSpec], Awaitable[Any]] | None = None,
    *,
    is_retryable: Callable[[Exception], bool] = is_retryable_error,
    validator: Callable[[Any], Any] | None = None,
    **kwargs: Any,
) -> ExecutionResult[Any]:
    """Execute down a fallback chain using provider registry dispatch.

    When ``handler`` is provided, it is called instead of the provider adapter.
    This is used by the RankLLM bridge and other custom execution paths.
    """
    from ..telemetry import create_llm_operation_span, set_span_error, set_span_success

    errors: list[tuple[ModelSpec, Exception]] = []

    for spec in chain.models:
        t0 = time.perf_counter()
        # The catalog default is the floor; an explicit caller timeout (e.g.
        # the adaptive 60s decision budget) widens the per-attempt cap. The
        # adapters receive the same value, so client and outer cap agree.
        per_attempt_timeout = kwargs.get("timeout_seconds") or spec.default_timeout
        try:
            with create_llm_operation_span(
                operation,
                system=spec.provider,
                attributes={"llm.model_name": spec.model_id},
            ) as span:
                try:
                    if handler is not None:
                        result = await asyncio.wait_for(
                            handler(spec),
                            timeout=per_attempt_timeout,
                        )
                    else:
                        adapter = get_provider(spec.provider)
                        result = await asyncio.wait_for(
                            adapter.execute(spec, **kwargs),
                            timeout=per_attempt_timeout,
                        )
                    if validator is not None:
                        result = validator(result)
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
