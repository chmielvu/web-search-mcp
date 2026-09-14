"""Provider-neutral acquisition, processing, and resolver contracts."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal
from urllib.parse import SplitResult, urlsplit

import httpx

ProcessingMode = Literal["agent", "index"]
ContentStatus = Literal["success", "partial", "blocked", "unsupported", "error"]
TextFormat = Literal["markdown", "html", "text"]
PROCESSING_POLICY_VERSION = "markdown-source-v2"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A processing or acquisition finding with optional one-based source lines."""

    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    source: str = "content"
    start_line: int | None = None
    end_line: int | None = None
    phase: str = "source"


@dataclass(frozen=True, slots=True)
class ContentError:
    """A stable failure independent of a provider's exception type."""

    code: str
    message: str
    retryable: bool = False
    http_status: int | None = None
    status: ContentStatus | None = None


class AcquisitionError(RuntimeError):
    """An acquisition failure retained by the orchestrator for final selection."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status: ContentStatus = "error",
        http_status: int | None = None,
    ) -> None:
        self.error = ContentError(
            code,
            message,
            retryable=retryable,
            http_status=http_status,
            status=status,
        )
        self.status = status


@dataclass(frozen=True, slots=True)
class TextDocument:
    """Text with an explicit syntax; plain text is never interpreted as Markdown."""

    text: str
    format: TextFormat = "markdown"
    language: str | None = None


@dataclass(frozen=True, slots=True)
class ThreadMessage:
    """One source message, retaining its identity and reply relationship."""

    id: str
    body: str
    author: str | None = None
    body_format: TextFormat = "markdown"
    role: str = "comment"
    created_at: str | None = None
    score: int | None = None
    accepted: bool = False
    permalink: str | None = None
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ThreadDocument:
    """A source-ordered thread shared by forum and developer-platform adapters."""

    title: str
    url: str
    messages: tuple[ThreadMessage, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PackageDocument:
    """Registry metadata and documentation, including the declared README syntax."""

    name: str
    url: str
    registry: str
    summary: str = ""
    version: str | None = None
    readme: TextDocument = field(default_factory=lambda: TextDocument(""))
    dependencies: tuple[str, ...] = ()
    links: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RepositoryDocument:
    """Repository-specific facts without pretending the repository is a package."""

    name: str
    url: str
    readme: TextDocument = field(default_factory=lambda: TextDocument(""))
    files: tuple[str, ...] = ()
    links: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawDocument:
    """An acquired source with no global cleanup, quality score, or final status."""

    input_url: str
    fetched_url: str
    source_type: str
    fetch_backend: str
    body: TextDocument | ThreadDocument | PackageDocument | RepositoryDocument
    content_type: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    links: tuple[dict[str, Any], ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    http_status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    complete: bool | None = None
    scope: Literal["full", "metadata", "excerpt", "sample"] = "full"
    bytes_downloaded: int | None = None
    redirect_count: int | None = None
    coverage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Measured structural usability, independent of acquisition backend identity."""

    accepted: bool
    score: float
    word_count: int
    heading_count: int
    code_blocks: int
    tables: int
    duplicate_ratio: float
    boilerplate_hits: int
    malformed_tables: int
    fence_errors: int
    flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcessedMarkdown:
    """The exact evaluated Markdown and its reusable structural evidence."""

    markdown: str
    quality: QualityReport
    diagnostics: tuple[Diagnostic, ...]
    transforms: tuple[str, ...]
    links: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    """An acquisition result joined to its one shared processing result."""

    document: RawDocument
    processed: ProcessedMarkdown
    attempt_index: int
    failure: ContentError | None = None


@dataclass(frozen=True, slots=True)
class FetchOptions:
    """Single-resource processing options shared by MCP and CLI entrypoints."""

    max_response_bytes: int = 5 * 1024 * 1024
    processing_mode: ProcessingMode = "agent"

    def cache_fingerprint(self) -> str:
        """Identify content-affecting options and the processing policy."""
        value = f"{PROCESSING_POLICY_VERSION}:{self.processing_mode}:{self.max_response_bytes}"
        return sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class FetchContext:
    """Borrowed transport and one absolute deadline for a resource acquisition."""

    http_client: httpx.AsyncClient
    deadline: float
    max_response_bytes: int
    processing_mode: ProcessingMode = "agent"

    def timeout(self, maximum: float = 30.0) -> float:
        """Return a timeout bounded by the remaining request deadline."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Content acquisition deadline exhausted")
        return min(maximum, remaining)


@dataclass(frozen=True, slots=True)
class ParsedURL:
    """A URL split once before matching ordered resolver specifications."""

    url: str
    parts: SplitResult

    @classmethod
    def parse(cls, url: str) -> ParsedURL:
        """Split a URL for pure resolver matching."""
        return cls(url=url, parts=urlsplit(url))


@dataclass(frozen=True, slots=True)
class ResolverTarget:
    """A matched provider resource passed directly to its acquisition adapter."""

    url: str
    kind: str
    values: dict[str, str] = field(default_factory=dict)
    allow_generic: bool = True


@dataclass(frozen=True, slots=True)
class ResolverSpec:
    """A static resolver entry, without plugin discovery or artifact construction."""

    name: str
    match: Callable[[ParsedURL], ResolverTarget | None]
    fetch: Callable[[ResolverTarget, FetchContext], Awaitable[RawDocument]]
