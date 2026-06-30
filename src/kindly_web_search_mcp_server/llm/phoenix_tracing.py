"""Phoenix/OpenInference helpers for LiteLLM-backed model calls.

The LiteLLM instrumentor creates the actual LLM span. This module only
propagates OpenInference context attributes and builds attribute payloads that
match Phoenix/OpenInference conventions.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import json
from typing import Any

try:
    from openinference.instrumentation import (
        get_attributes_from_context,
        using_attributes,
    )
except Exception:  # pragma: no cover - optional tracing dependency
    def get_attributes_from_context() -> dict[str, Any]:
        return {}

    @contextmanager
    def using_attributes(**attrs: Any) -> Iterator[None]:
        yield

from opentelemetry import trace

OPENINFERENCE_SPAN_KIND = "openinference.span.kind"
OPENINFERENCE_SPAN_KIND_LLM = "LLM"
OPENINFERENCE_SPAN_KIND_CHAIN = "CHAIN"
OPENINFERENCE_SPAN_KIND_TOOL = "TOOL"
OPENINFERENCE_SPAN_KIND_RETRIEVER = "RETRIEVER"
OPENINFERENCE_SPAN_KIND_RERANKER = "RERANKER"

LLM_SYSTEM = "llm.system"
LLM_MODEL_NAME = "llm.model_name"
LLM_INVOCATION_PARAMETERS = "llm.invocation_parameters"
LLM_INPUT_MESSAGES = "llm.input_messages"
LLM_OUTPUT_MESSAGES = "llm.output_messages"
LLM_TOKEN_COUNT_PROMPT = "llm.token_count.prompt"
LLM_TOKEN_COUNT_COMPLETION = "llm.token_count.completion"
LLM_TOKEN_COUNT_TOTAL = "llm.token_count.total"
INPUT_VALUE = "input.value"
INPUT_MIME_TYPE = "input.mime_type"
OUTPUT_VALUE = "output.value"
OUTPUT_MIME_TYPE = "output.mime_type"
SESSION_ID = "session.id"
USER_ID = "user.id"
METADATA = "metadata"
TAG_TAGS = "tag.tags"


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


def openinference_context_attributes(
    ctx: LLMTraceContext | None,
) -> dict[str, Any]:
    """Return Phoenix/OpenInference context attributes for a span."""
    if ctx is None:
        return {}

    attributes: dict[str, Any] = {}
    if ctx.session_id:
        attributes[SESSION_ID] = ctx.session_id
    if ctx.user_id:
        attributes[USER_ID] = ctx.user_id
    if ctx.metadata:
        metadata = dict(ctx.metadata)
        if ctx.trace_name:
            metadata["trace_name"] = ctx.trace_name
        attributes[METADATA] = json.dumps(metadata, sort_keys=True, default=str)
    elif ctx.trace_name:
        attributes[METADATA] = json.dumps(
            {"trace_name": ctx.trace_name}, sort_keys=True, default=str
        )
    if ctx.tags:
        attributes[TAG_TAGS] = list(ctx.tags)
    return attributes


@contextmanager
def openinference_context_scope(ctx: LLMTraceContext | None) -> Iterator[None]:
    """Propagate OpenInference context attributes for the current span tree."""
    if ctx is None:
        yield
        return

    attrs: dict[str, Any] = {}
    if ctx.session_id:
        attrs["session_id"] = ctx.session_id
    if ctx.user_id:
        attrs["user_id"] = ctx.user_id
    if ctx.metadata:
        metadata = dict(ctx.metadata)
        if ctx.trace_name:
            metadata = {**metadata, "trace_name": ctx.trace_name}
        attrs["metadata"] = metadata
    elif ctx.trace_name:
        attrs["metadata"] = {"trace_name": ctx.trace_name}
    if ctx.tags:
        attrs["tags"] = list(ctx.tags)

    with using_attributes(**attrs):
        yield


def current_openinference_attributes() -> dict[str, Any]:
    """Read the current OpenInference context attributes."""
    return dict(get_attributes_from_context())


def set_trace_context_attributes(ctx: LLMTraceContext | None) -> None:
    """Backward-compatible helper that applies context to the current span.

    Prefer :func:`openinference_context_scope` for new code.
    """
    if ctx is None:
        return
    span = trace.get_current_span()
    if not span.is_recording():
        return
    span.set_attributes(current_openinference_attributes())
    span.set_attributes(openinference_context_attributes(ctx))
