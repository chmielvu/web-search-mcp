"""Shared glue for bridge producers whose upstream adapter returns Markdown.

Bridge producers wrap a resolver-level async function returning a
``{markdown, complete, coverage}`` payload and map it onto a neutral
:class:`RawDocument`; failures surface as ``<backend>_fetch_failed``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    RawDocument,
    ResolverTarget,
    TextDocument,
)


def _upstream_note(backend: str) -> tuple[Diagnostic, ...]:
    return (
        Diagnostic(
            code="upstream_normalized",
            message=f"Source body arrived normalized by the {backend} adapter.",
            source=backend,
            phase="acquire",
        ),
    )


def _text_document(
    target_url: str,
    backend: str,
    text: str,
    *,
    fetched_url: str | None = None,
    content_type: str | None = None,
    scope: str = "full",
    complete: bool | None = True,
    coverage: dict[str, object] | None = None,
    extra: tuple[Diagnostic, ...] = (),
) -> RawDocument:
    if not text.strip():
        raise AcquisitionError(code="empty_content", message=f"{backend} returned no text.")
    return RawDocument(
        input_url=target_url,
        fetched_url=fetched_url or target_url,
        source_type=backend,
        fetch_backend=f"{backend}_api",
        body=TextDocument(text=text, format="markdown"),
        content_type=content_type or "text/markdown",
        title=None,
        metadata={},
        links=(),
        diagnostics=_upstream_note(backend) + tuple(extra),
        http_status=None,
        response_headers={},
        complete=complete,
        scope=scope,  # type: ignore[arg-type]
        bytes_downloaded=None,
        redirect_count=None,
        coverage=dict(coverage or {}),
    )


def _bridge_document(
    target: ResolverTarget,
    backend: str,
    payload: dict[str, object],
) -> RawDocument:
    """Map a ``{markdown, complete, coverage}`` adapter payload to a RawDocument."""
    complete = payload.get("complete")
    coverage = payload.get("coverage")
    return _text_document(
        target.values.get("url") or target.url,
        backend,
        str(payload.get("markdown") or ""),
        complete=complete if isinstance(complete, bool) else None,
        coverage=coverage if isinstance(coverage, dict) else None,
    )


async def bridge_text_producer(
    target: ResolverTarget,
    ctx: FetchContext,
    backend: str,
    acquire: Callable[[str], Awaitable[dict[str, object]]],
) -> RawDocument:
    """Run one Markdown-returning adapter and wrap failures as AcquisitionError."""
    url = target.values.get("url") or target.url
    try:
        payload = await acquire(url)
    except Exception as exc:
        raise AcquisitionError(code=f"{backend}_fetch_failed", message=str(exc)[:300]) from exc
    return _bridge_document(target, backend, payload)
