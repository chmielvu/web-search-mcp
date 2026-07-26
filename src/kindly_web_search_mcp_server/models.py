"""Pydantic response models for MCP tool outputs.

P2 Pattern: Typed Pydantic output schemas from Brave/Tavily MCP
- Better agent schema inference through proper type hints
- Provider tracking: providers_used field
- Partial failure handling: warnings field
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# EntitySpan imported lazily in type hints to keep models light (entity core is pure)
from .entity.models import EntitySpan  # always available (pure python)


# ============================================================================
# Core Result Types
# ============================================================================


class WebSearchResult(BaseModel):
    """Single search result from web search."""

    title: str
    link: str
    snippet: str
    domain: str | None = None
    mime_hint: str | None = Field(
        default=None,
        description="Best-effort MIME hint when known.",
    )
    published_date: str | None = None
    source_engines: list[str] | None = Field(
        default=None,
        description="Provider engine names that surfaced the result, when known.",
    )
    category: str | None = None
    raw_score: float | None = Field(
        default=None,
        description="Unnormalized score returned by the provider before merge/rerank.",
    )
    providers: list[str] | None = None
    provider_count: int | None = Field(
        default=None,
        description="Number of providers that surfaced this result (agreement signal).",
    )
    score: float | None = Field(
        default=None,
        description="Merged/reranked score used for final ordering.",
    )
    provider_consensus_rrf_score: float | None = Field(
        default=None,
        description="Deprecated: previously held a separate first-stage RRF score. "
        "Now None; the pipeline uses a single fused RRF pass with BM25.",
    )
    hybrid_rrf_score: float | None = Field(
        default=None,
        description="Single-stage RRF score incorporating provider rankings and BM25 lexical signal.",
    )
    cross_relevance_score: float | None = Field(
        default=None,
        description="Raw cross-encoder relevance score.",
    )
    entities: list[EntitySpan] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class ProviderWarning(BaseModel):
    """Warning about a partial failure from a provider."""

    provider: str
    error: str
    error_type: str | None = None


class ContentLink(BaseModel):
    """Single discovered link from a page or sitemap."""

    url: str
    text: str
    domain: str | None = None
    internal: bool = Field(
        default=False, description="Whether the link stays within the source domain."
    )


# ============================================================================
# Tool Response Models
# ============================================================================


class WebSearchResponse(BaseModel):
    """Response from web_search tool."""

    query: str
    results: list[WebSearchResult] = Field(default_factory=list)
    total_results: int = 0
    providers_used: list[str] = Field(
        default_factory=list,
        description="Providers that successfully returned results.",
    )
    warnings: list[ProviderWarning] | None = None
    diagnostics: list[dict[str, Any]] | None = None
    intent: str | None = None
    query_shaping: list[dict[str, Any]] | None = None


class GetContentResponse(BaseModel):
    """Response from get_content tool."""

    input_url: str
    normalized_url: str
    fetched_url: str | None = None
    status: str = Field(
        description="Fetch status: success, partial, blocked, unsupported, or error."
    )
    source_type: str = Field(description="Detected source type, e.g. html, pdf, github_issue.")
    fetch_backend: str = Field(description="Backend strategy used to retrieve content.")
    page_content: str
    window: dict[str, Any]
    metadata: dict[str, Any] | None = None
    links: list[ContentLink] | None = None
    continuation_notice: str | None = None
    content_type: str | None = None
    error: dict[str, Any] | None = None
    entities: list[EntitySpan] | None = None
    summary: dict[str, Any] | None = None
    content_quality: str | None = Field(
        default=None,
        description="Content quality classification: success, partial, blocked, error, or unsupported.",
    )
    content_word_count: int | None = Field(
        default=None, description="Word count of the full fetched page."
    )


class BatchContentResult(BaseModel):
    """Single item in batch_get_content output."""

    input_url: str
    normalized_url: str
    fetched_url: str | None = None
    status: str
    source_type: str
    fetch_backend: str
    page_content: str
    window: dict[str, Any]
    content_type: str | None = None
    metadata: dict[str, Any] | None = None
    links: list[ContentLink] | None = None
    continuation_notice: str | None = None
    error: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    content_quality: str | None = Field(
        default=None,
        description="Content quality classification: success, partial, blocked, error, or unsupported.",
    )
    content_word_count: int | None = Field(
        default=None, description="Word count of the full fetched page."
    )


class DiscoverLinksResponse(BaseModel):
    """Response from discover_links tool."""

    input_url: str
    normalized_url: str
    fetched_url: str | None = None
    source_type: str
    links: list[ContentLink] = Field(default_factory=list)
    returned_links: int = 0
    has_more: bool = Field(
        default=False, description="Whether more links exist beyond the current page."
    )
    metadata: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class BatchGetContentResponse(BaseModel):
    """Response from batch_get_content tool."""

    results: list[BatchContentResult] = Field(default_factory=list)
    total_requested: int = 0
    total_returned: int = 0
    total_chars_returned: int = 0
    has_more: bool = False
    cursor: str | None = None


class GeminiSearchResponse(BaseModel):
    """Response from gemini_search tool (AI-grounded search)."""

    query: str
    answer: str
    web_search_queries: list[str] | None = None
    grounding_chunks: list[dict[str, Any]] | None = None
    structured_result: dict[str, Any] | None = None
    error: str | None = None


class GrokCitation(BaseModel):
    """Single citation from grok_search result (OpenRouter url_citation)."""

    url: str
    title: str | None = None
    snippet: str | None = None


class GrokSearchResponse(BaseModel):
    """Response from grok_search tool — Grok 4.3 via OpenRouter web+X search."""

    query: str
    answer: str
    citations: list[GrokCitation] = Field(default_factory=list)
    model: str
    search_queries_used: int = 0
    error: str | None = None


class YouTubeTranscriptResponse(BaseModel):
    """Response from youtube_transcript tool."""

    video_id: str
    video_url: str
    title: str | None = None
    transcript_text: str
    language: str
    is_translated: bool = False
    duration_seconds: float | None = None
    transcript_segments: list[dict[str, Any]] | None = None
    error: str | None = None


class YouTubeSearchResponse(BaseModel):
    """Response from youtube_search tool."""

    query: str
    results: list[WebSearchResult] = Field(default_factory=list)
    total_results: int = 0
    search_backend: str | None = None  # "api" or "searxng"


class SimilarLinkResult(BaseModel):
    """Single related URL returned by Composio Similarlinks."""

    title: str
    link: str
    score: float | None = None


class SimilarLinksResponse(BaseModel):
    """Response from Composio Similarlinks."""

    url: str
    results: list[SimilarLinkResult] = Field(default_factory=list)
    total_results: int = 0


class ImageSearchResult(BaseModel):
    """Single image metadata result from Composio Image Search."""

    title: str
    source: str | None = None
    page_link: str
    original_url: str
    thumbnail_url: str | None = None


class ImageSearchResponse(BaseModel):
    """Response from Composio Image Search."""

    query: str
    results: list[ImageSearchResult] = Field(default_factory=list)
    total_results: int = 0
    page: int = 0


# ============================================================================
# Error Response Model
# ============================================================================


class ToolErrorResponse(BaseModel):
    """MCP-compliant error response.

    MCP spec requires isError: true for tool execution failures.
    This model ensures consistent error responses across all tools.
    """

    error: str = Field(description="Human-readable error message.")
    error_type: str = Field(
        default="unknown",
        description="Error classification: rate_limit, auth, network, content, config, unknown.",
    )
    isError: bool = Field(default=True, description="MCP protocol: must be True for errors.")
    action: str | None = Field(
        default=None,
        description="Actionable guidance for the agent.",
    )
    provider: str | None = Field(
        default=None,
        description="Provider that caused the error.",
    )
    status_code: int | None = Field(
        default=None,
        description="HTTP status code if applicable.",
    )
    retry_after: int | None = Field(
        default=None,
        description="Seconds to wait before retrying (for rate limits).",
    )

    @classmethod
    def from_structured_error(cls, structured: dict[str, Any]) -> "ToolErrorResponse":
        """Create from StructuredToolError.to_dict()."""
        return cls(**structured)


# ============================================================================
# Union Types for Tool Signatures
# ============================================================================

# Type unions for tool return type annotations
# These provide better schema inference for agents

WebSearchResultType = WebSearchResponse | ToolErrorResponse
GetContentResultType = GetContentResponse | ToolErrorResponse
GeminiSearchResultType = GeminiSearchResponse | ToolErrorResponse
GrokSearchResultType = GrokSearchResponse | ToolErrorResponse
YouTubeTranscriptResultType = YouTubeTranscriptResponse | ToolErrorResponse
YouTubeSearchResultType = YouTubeSearchResponse | ToolErrorResponse
SimilarLinksResultType = SimilarLinksResponse | ToolErrorResponse
ImageSearchResultType = ImageSearchResponse | ToolErrorResponse


# ============================================================================
# Academic Search Result Types
# ============================================================================


class AcademicPaper(BaseModel):
    """A single academic paper from scholarly search."""

    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    citations: int | None = None
    url: str
    pdf_url: str | None = None
    source: str = Field(description="Provider: semanticscholar or arxiv.")
    source_id: str
    external_ids: dict[str, str] | None = None
    fields_of_study: list[str] | None = None
    is_open_access: bool | None = None
    score: float | None = None


class AcademicSearchResponse(BaseModel):
    """Response from academic_search tool."""

    query: str
    results: list[AcademicPaper] = Field(default_factory=list)
    total_results: int = 0
    sources_used: list[str] = Field(default_factory=list)
    warnings: list[ProviderWarning] | None = None


AcademicSearchResultType = AcademicSearchResponse | ToolErrorResponse
