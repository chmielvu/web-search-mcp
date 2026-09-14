"""Final content artifacts, selected-source enrichment, and index persistence."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from kindly_web_search_mcp_server.content.models import (
    PROCESSING_POLICY_VERSION,
    Candidate,
    ContentError,
    ContentStatus,
    Diagnostic,
    FetchOptions,
    ProcessingMode,
    QualityReport,
)
from kindly_web_search_mcp_server.settings import settings
from kindly_web_search_mcp_server.utils.entity import EntitySpan
from kindly_web_search_mcp_server.utils.observability import emit_observability_event
from kindly_web_search_mcp_server.utils.paths import OUTPUTS_DIR
from kindly_web_search_mcp_server.utils.url_canonicalize import canonicalize_url

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContentArtifact:
    """The selected and finalized content; constructed only by finalize_artifact."""

    input_url: str
    normalized_url: str
    fetched_url: str | None
    status: ContentStatus
    source_type: str
    fetch_backend: str
    markdown: str
    quality: QualityReport
    processing_mode: ProcessingMode
    content_type: str | None = None
    title: str | None = None
    metadata: dict[str, Any] | None = None
    links: tuple[dict[str, Any], ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    transforms: tuple[str, ...] = ()
    entities: tuple[EntitySpan, ...] | None = None
    error: ContentError | None = None
    cached: bool = False
    complete: bool | None = None
    scope: str = "full"
    bytes_downloaded: int | None = None
    redirect_count: int | None = None
    output_path: str | None = None
    policy_version: str = PROCESSING_POLICY_VERSION
    coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def total_chars(self) -> int:
        """Return the length of the exact finalized content."""
        return len(self.markdown)


def _write_index_document(markdown: str, normalized_url: str) -> str:
    """Atomically persist a content-addressed Markdown file in the fixed outputs root."""
    content = markdown.encode("utf-8")
    digest = sha256(normalized_url.encode("utf-8"))
    digest.update(b"\0")
    digest.update(content)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    destination = OUTPUTS_DIR / f"{digest.hexdigest()}.md"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=OUTPUTS_DIR, suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return str(destination.resolve())


def _candidate_outcome(candidate: Candidate) -> tuple[ContentStatus, ContentError | None]:
    """Resolve final status from existing source evidence without reclassification."""
    failure = candidate.failure
    if failure is not None and failure.status:
        return failure.status, failure
    document = candidate.document
    report = candidate.processed.quality
    # Origin transport facts outrank the processor's text-only acceptance:
    # a 401/403/429/5xx response is never a success even when its body
    # parses as Markdown.
    status_code = document.http_status
    if status_code is not None and status_code >= 400:
        origin_status: ContentStatus = "blocked" if status_code in {401, 403, 429} else "error"
        return origin_status, ContentError(
            f"http_{status_code}",
            f"The source returned HTTP {status_code}.",
            status_code == 429 or status_code >= 500,
            status_code,
            origin_status,
        )
    if report.accepted:
        return "success", None
    flags = set(report.flags)
    if flags.intersection({"login_wall", "paywall", "access_blocked", "bot_challenge"}):
        code = next(
            flag
            for flag in report.flags
            if flag in {"login_wall", "paywall", "access_blocked", "bot_challenge"}
        )
        return "blocked", ContentError(
            code, "The source did not expose accessible content.", status="blocked"
        )
    if not candidate.processed.markdown.strip() or "garbled_content" in flags:
        status = "error"
        return status, ContentError(
            "empty_content" if not candidate.processed.markdown.strip() else "garbled_content",
            "No usable source content was acquired.",
            status=status,
        )
    code = report.flags[0] if report.flags else "content_quality"
    return "partial", ContentError(
        code,
        "Content was retained with processing or coverage limitations.",
        status="partial",
    )


async def finalize_artifact(
    input_url: str,
    candidate: Candidate | None,
    *,
    options: FetchOptions,
    failure: ContentError | None = None,
    failure_status: ContentStatus = "error",
    cached: bool = False,
    cached_entities: tuple[EntitySpan, ...] | None = None,
    extra_diagnostics: tuple[Diagnostic, ...] = (),
    deadline: float | None = None,
) -> ContentArtifact:
    """Enrich and persist the selected content before constructing its sole artifact."""
    normalized_url = canonicalize_url(input_url)
    document = candidate.document if candidate is not None else None
    processed = candidate.processed if candidate is not None else None
    markdown = processed.markdown if processed is not None else ""
    quality = (
        processed.quality
        if processed is not None
        else QualityReport(
            accepted=False,
            score=0.0,
            word_count=0,
            heading_count=0,
            code_blocks=0,
            tables=0,
            duplicate_ratio=0.0,
            boilerplate_hits=0,
            malformed_tables=0,
            fence_errors=0,
            flags=(failure.code if failure else "acquisition_failed",),
        )
    )
    status, error = (
        _candidate_outcome(candidate)
        if candidate is not None
        else (
            failure_status,
            failure
            or ContentError(
                "acquisition_failed",
                "No acquisition path returned content.",
                status=failure_status,
            ),
        )
    )
    diagnostics = list(processed.diagnostics if processed is not None else ())
    diagnostics.extend(extra_diagnostics)
    entities = cached_entities
    if (
        settings.entity_extraction_enabled
        and markdown
        and status in {"success", "partial"}
        and entities is None
    ):
        remaining = deadline - time.monotonic() if deadline is not None else 15.0
        if remaining > 0:
            try:
                from kindly_web_search_mcp_server.ml.gliner_client import get_gliner_client

                async with asyncio.timeout(min(15.0, remaining)):
                    extracted = await get_gliner_client().extract_entities(markdown)
                entities = tuple(extracted or ())
                emit_observability_event(
                    LOGGER,
                    "entity.content_extracted",
                    url=input_url,
                    count=len(entities),
                    backend=document.fetch_backend if document else "none",
                )
            except Exception as exc:
                diagnostics.append(
                    Diagnostic(
                        "entity_extraction_failed",
                        f"Optional entity extraction failed: {type(exc).__name__}",
                        source="enrichment",
                    )
                )
                emit_observability_event(
                    LOGGER,
                    "entity.extraction.error",
                    url=input_url,
                    error=type(exc).__name__,
                    component="content_finalizer",
                )
        else:
            diagnostics.append(
                Diagnostic(
                    "entity_extraction_skipped",
                    "The request deadline left no time for optional entity extraction.",
                    source="enrichment",
                )
            )
    output_path = None
    if options.processing_mode == "index" and markdown and status in {"success", "partial"}:
        try:
            output_path = await asyncio.to_thread(_write_index_document, markdown, normalized_url)
        except OSError as exc:
            diagnostics.append(
                Diagnostic(
                    "index_output_failed",
                    f"Could not persist index content: {exc}",
                    severity="error",
                    source="index",
                )
            )
            status = "partial"
            error = ContentError(
                "index_output_failed", "Content was fetched but could not be written to outputs."
            )
    return ContentArtifact(
        input_url=input_url,
        normalized_url=normalized_url,
        fetched_url=document.fetched_url if document is not None else None,
        status=status,
        source_type=document.source_type if document is not None else "unknown",
        fetch_backend=document.fetch_backend if document is not None else "none",
        markdown=markdown,
        quality=quality,
        processing_mode=options.processing_mode,
        content_type=document.content_type if document is not None else None,
        title=document.title if document is not None else None,
        metadata=document.metadata if document is not None else None,
        links=processed.links if processed is not None else (),
        diagnostics=tuple(diagnostics),
        transforms=processed.transforms if processed is not None else (),
        entities=entities,
        error=error,
        cached=cached,
        complete=document.complete if document is not None else None,
        scope=document.scope if document is not None else "full",
        bytes_downloaded=document.bytes_downloaded if document is not None else None,
        redirect_count=document.redirect_count if document is not None else None,
        coverage=dict(document.coverage) if document is not None else {},
        output_path=output_path,
    )


def artifact_to_dict(artifact: ContentArtifact) -> dict[str, Any]:
    """Serialize the complete internal artifact without repeating or dropping its evidence."""
    payload = asdict(artifact)
    payload["entities"] = (
        [entity.model_dump(exclude_none=True) for entity in artifact.entities]
        if artifact.entities is not None
        else None
    )
    return payload


def rehydrate_cached_artifact(
    envelope: dict[str, Any], normalized_url: str
) -> ContentArtifact | None:
    """Restore an exact-evidence artifact from a versioned cache envelope.

    This is the only construction site besides :func:`finalize_artifact`;
    it replays stored quality, diagnostics, and enrichment verbatim without
    reprocessing, and rejects version-mismatched envelopes as misses.
    """
    if not isinstance(envelope, dict):
        return None
    if envelope.get("policy_version") != PROCESSING_POLICY_VERSION:
        return None
    payload = envelope.get("artifact")
    if not isinstance(payload, dict) or not isinstance(payload.get("normalized_url"), str):
        return None
    quality_payload = payload.get("quality") or {}
    quality = QualityReport(
        accepted=bool(quality_payload.get("accepted", False)),
        score=float(quality_payload.get("score", 0.0) or 0.0),
        word_count=int(quality_payload.get("word_count", 0) or 0),
        heading_count=int(quality_payload.get("heading_count", 0) or 0),
        code_blocks=int(quality_payload.get("code_blocks", 0) or 0),
        tables=int(quality_payload.get("tables", 0) or 0),
        duplicate_ratio=float(quality_payload.get("duplicate_ratio", 0.0) or 0.0),
        boilerplate_hits=int(quality_payload.get("boilerplate_hits", 0) or 0),
        malformed_tables=int(quality_payload.get("malformed_tables", 0) or 0),
        fence_errors=int(quality_payload.get("fence_errors", 0) or 0),
        flags=tuple(quality_payload.get("flags") or ()),
    )
    error_payload = payload.get("error")
    error_obj: ContentError | None = None
    if isinstance(error_payload, dict):
        http_status = error_payload.get("http_status")
        raw_status = error_payload.get("status")
        error_status: ContentStatus = (
            raw_status
            if raw_status in ("success", "partial", "blocked", "unsupported", "error")
            else "error"
        )
        error_obj = ContentError(
            str(error_payload.get("code") or "cache_error"),
            str(error_payload.get("message") or ""),
            bool(error_payload.get("retryable", False)),
            int(http_status) if isinstance(http_status, int) else None,
            error_status,
        )
    links_raw = payload.get("links") or ()
    metadata = payload.get("metadata")
    diagnostics_raw = payload.get("diagnostics") or ()
    entities_raw = payload.get("entities")
    entities: tuple[EntitySpan, ...] | None = None
    if isinstance(entities_raw, list):
        restored: list[EntitySpan] = []
        for entry in entities_raw:
            if isinstance(entry, dict):
                try:
                    restored.append(EntitySpan(**entry))
                except Exception:
                    continue
        entities = tuple(restored) or None
    restored_diagnostics: list[Diagnostic] = []
    if isinstance(diagnostics_raw, (list, tuple)):
        for entry in diagnostics_raw:
            if isinstance(entry, dict):
                try:
                    restored_diagnostics.append(Diagnostic(**entry))
                except Exception:
                    continue
    raw_status = payload.get("status")
    status: ContentStatus = (
        raw_status
        if raw_status in ("success", "partial", "blocked", "unsupported", "error")
        else "error"
    )
    mode: ProcessingMode = "index" if payload.get("processing_mode") == "index" else "agent"
    return ContentArtifact(
        input_url=str(payload.get("input_url") or normalized_url),
        normalized_url=str(payload.get("normalized_url") or normalized_url),
        fetched_url=payload.get("fetched_url"),
        status=status,
        source_type=str(payload.get("source_type") or "cache"),
        fetch_backend="cache",
        markdown=str(payload.get("markdown") or ""),
        quality=quality,
        processing_mode=mode,
        content_type=payload.get("content_type"),
        title=payload.get("title"),
        metadata=metadata if isinstance(metadata, dict) else None,
        links=tuple(links_raw) if isinstance(links_raw, (list, tuple)) else (),
        diagnostics=tuple(restored_diagnostics),
        transforms=tuple(payload.get("transforms") or ()),
        entities=entities,
        error=error_obj,
        cached=True,
        complete=payload.get("complete"),
        scope=str(payload.get("scope") or "full"),
        bytes_downloaded=payload.get("bytes_downloaded"),
        redirect_count=payload.get("redirect_count"),
        coverage=dict(payload.get("coverage") or {})
        if isinstance(payload.get("coverage"), dict)
        else {},
        output_path=payload.get("output_path"),
    )
