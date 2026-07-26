"""Generic fallback execution engine across provider specifications."""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from contextvars import Token
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
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


def _is_retryable_error(exc: Exception) -> bool:
    """Return whether a provider failure should advance the fallback chain.

    Transport failures, timeouts, rate limits, conflicts, and server errors
    are transient.  Authentication, permission, request-validation, and
    local configuration errors are deterministic and should surface instead of
    making every fallback provider repeat the same invalid request.
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
    openai_non_retryable = (
        AuthenticationError,
        BadRequestError,
        NotFoundError,
        PermissionDeniedError,
        UnprocessableEntityError,
    )

    if isinstance(exc, openai_retryable):
        return True
    if isinstance(exc, openai_non_retryable):
        return False

    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code in {408, 409, 429} or status_code >= 500

    error_name = type(exc).__name__.casefold()
    if any(
        marker in error_name
        for marker in (
            "authentication",
            "authorization",
            "permission",
            "badrequest",
            "invalidrequest",
            "validation",
            "notfound",
        )
    ):
        return False
    return True


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
    chain: ChainSpec,
    operation: str,
    handler: Callable[[ModelSpec], Awaitable[Any]] | None = None,
    *,
    is_retryable: Callable[[Exception], bool] = _is_retryable_error,
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
                            timeout=spec.default_timeout,
                        )
                    else:
                        adapter = get_provider(spec.provider)
                        result = await asyncio.wait_for(
                            adapter.execute(spec, **kwargs),
                            timeout=spec.default_timeout,
                        )
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
