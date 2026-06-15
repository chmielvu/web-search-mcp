"""Phoenix/OTel helpers for LiteLLM-backed model calls.

With Arize Phoenix, tracing is handled via OpenTelemetry auto-instrumentation
(openinference-instrumentation-litellm). No Langfuse SDK callbacks or per-call
kwargs are needed. OTel context propagation handles trace correlation.

LLMTraceContext is a lightweight dataclass for setting OTel span attributes
at call sites that want to annotate their spans with metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from opentelemetry import trace


@dataclass(frozen=True, slots=True)
class LLMTraceContext:
    """Context propagated into OTel spans for LLM calls.

    Unlike the old LangfuseTraceContext, this does NOT require passing
    credentials or metadata through LiteLLM kwargs. Instead, it sets
    OTel span attributes on the current span.
    """

    trace_name: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None


def set_trace_context_attributes(ctx: LLMTraceContext | None) -> None:
    """Set OTel span attributes from an LLMTraceContext on the current span."""
    if ctx is None:
        return
    span = trace.get_current_span()
    if not span.is_recording():
        return
    if ctx.trace_name:
        span.set_attribute("gen_ai.trace_name", ctx.trace_name)
    if ctx.session_id:
        span.set_attribute("session.id", ctx.session_id)
    if ctx.user_id:
        span.set_attribute("user.id", ctx.user_id)
    if ctx.tags:
        span.set_attribute("tags", ",".join(ctx.tags))
    if ctx.metadata:
        for key, value in ctx.metadata.items():
            if value is not None:
                span.set_attribute(f"metadata.{key}", str(value))
