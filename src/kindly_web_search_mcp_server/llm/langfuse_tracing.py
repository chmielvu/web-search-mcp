"""Langfuse helpers for LiteLLM-backed model calls."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from ..settings import resolve_langfuse_credentials, settings

_CALLBACK_LOCK = threading.Lock()
_CALLBACKS_CONFIGURED = False


@dataclass(frozen=True, slots=True)
class LangfuseTraceContext:
    """Context propagated into LiteLLM Langfuse traces."""

    trace_name: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None


def _normalize_callbacks(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def ensure_langfuse_litellm_callbacks() -> bool:
    """Install Langfuse callbacks on LiteLLM once credentials exist."""
    global _CALLBACKS_CONFIGURED
    if _CALLBACKS_CONFIGURED:
        return True

    lf_pk, lf_sk, _ = resolve_langfuse_credentials(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
        mcp_auth_header=settings.langfuse_mcp_auth_header,
    )
    if not lf_pk or not lf_sk:
        return False

    with _CALLBACK_LOCK:
        if _CALLBACKS_CONFIGURED:
            return True

        import litellm

        success_callbacks = _normalize_callbacks(
            getattr(litellm, "success_callback", None)
        )
        failure_callbacks = _normalize_callbacks(
            getattr(litellm, "failure_callback", None)
        )
        if "langfuse" not in success_callbacks:
            success_callbacks.append("langfuse")
        if "langfuse" not in failure_callbacks:
            failure_callbacks.append("langfuse")

        litellm.success_callback = success_callbacks
        litellm.failure_callback = failure_callbacks
        _CALLBACKS_CONFIGURED = True
        return True


def build_langfuse_litellm_kwargs(
    *,
    generation_name: str,
    trace_context: LangfuseTraceContext | None = None,
) -> dict[str, Any]:
    """Build per-request Langfuse credentials and metadata for LiteLLM."""
    lf_pk, lf_sk, lf_host = resolve_langfuse_credentials(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
        mcp_auth_header=settings.langfuse_mcp_auth_header,
    )
    if not lf_pk or not lf_sk:
        return {}

    metadata: dict[str, Any] = {"generation_name": generation_name}
    if trace_context is not None:
        if trace_context.trace_name:
            metadata["trace_name"] = trace_context.trace_name
        if trace_context.session_id:
            metadata["session_id"] = trace_context.session_id
        if trace_context.user_id:
            metadata["trace_user_id"] = trace_context.user_id
        if trace_context.tags:
            metadata["tags"] = [tag for tag in trace_context.tags if tag]
        if trace_context.metadata:
            metadata["trace_metadata"] = dict(trace_context.metadata)

    request_kwargs: dict[str, Any] = {
        "langfuse_public_key": lf_pk,
        "langfuse_secret_key": lf_sk,
    }
    if lf_host:
        request_kwargs["langfuse_host"] = lf_host
    request_kwargs["metadata"] = metadata
    return request_kwargs

