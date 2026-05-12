"""Pydantic response models for MCP tool outputs.

P2 Pattern: Typed Pydantic output schemas from Brave/Tavily MCP
- Better agent schema inference through proper type hints
- Provider tracking: providers_used field
- Partial failure handling: warnings field
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ============================================================================
# Core Result Types
# ============================================================================


class WebSearchResult(BaseModel):
    """Single search result from web search."""
    title: str = Field(description="Human-readable result title.")
    link: str = Field(description="Canonical URL for the result.")
    snippet: str = Field(description="Search engine snippet/preview text.")
    domain: str | None = Field(default=None, description="Domain associated with the result.")
    resource_type: str | None = Field(
        default=None,
        description="High-level resource type such as web, pdf, youtube, github, or other.",
    )
    mime_hint: str | None = Field(
        default=None,
        description="Best-effort MIME hint when known.",
    )
    providers: list[str] | None = Field(
        default=None,
        description="Search providers that surfaced this result.",
    )
    provider_count: int | None = Field(
        default=None,
        description="Number of providers that surfaced this result (agreement signal).",
    )
    score: float | None = Field(
        default=None,
        description="Merged/reranked score used for final ordering.",
    )
    diagnostics: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional diagnostics metadata emitted when KINDLY_DIAGNOSTICS is enabled.",
    )


class ProviderWarning(BaseModel):
    """Warning about a partial failure from a provider."""
    provider: str = Field(description="Provider that encountered the issue.")
    error: str = Field(description="Error message from the provider.")
    error_type: str | None = Field(default=None, description="Error classification if known.")


# ============================================================================
# Tool Response Models
# ============================================================================


class WebSearchResponse(BaseModel):
    """Response from web_search tool."""
    query: str = Field(description="Original raw query.")
    results: list[WebSearchResult] = Field(default_factory=list, description="Search results.")
    total_results: int = Field(default=0, description="Total number of results returned.")
    providers_used: list[str] = Field(
        default_factory=list,
        description="Providers that successfully returned results.",
    )
    warnings: list[ProviderWarning] | None = Field(
        default=None,
        description="Partial failures from providers (e.g., rate limits, timeouts).",
    )
    diagnostics: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional diagnostics metadata when KINDLY_DIAGNOSTICS is enabled.",
    )


class GetContentResponse(BaseModel):
    """Response from get_content tool."""
    input_url: str = Field(description="Exact URL supplied by the caller.")
    normalized_url: str = Field(description="Normalized URL used for cache lookup and deduplication.")
    fetched_url: str | None = Field(default=None, description="Actual URL reached after redirects, if known.")
    status: str = Field(description="Fetch status: success, partial, blocked, unsupported, or error.")
    source_type: str = Field(description="Detected source type, e.g. html, pdf, github_issue.")
    fetch_backend: str = Field(description="Backend strategy used to retrieve content.")
    page_content: str = Field(description="Bounded content slice for the requested window.")
    window: dict[str, Any] = Field(description="Window metadata for pagination/continuation.")
    content_type: str | None = Field(
        default=None,
        description="Detected HTTP content type if available.",
    )
    error: dict[str, Any] | None = Field(
        default=None,
        description="Structured error payload for non-success statuses.",
    )
    summary: dict[str, Any] | None = Field(
        default=None,
        description="Optional derived summary when summary_mode is requested.",
    )
    diagnostics: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional diagnostics metadata when KINDLY_DIAGNOSTICS is enabled.",
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
    query: str = Field(description="Original search query.")
    answer: str = Field(description="AI-synthesized answer with inline citations [N].")
    web_search_queries: list[str] | None = Field(
        default=None,
        description="Search queries used for grounding.",
    )
    grounding_chunks: list[dict[str, Any]] | None = Field(
        default=None,
        description="Grounding sources with citations.",
    )
    structured_result: dict[str, Any] | None = Field(
        default=None,
        description="Structured output when structured_output=True.",
    )
    error: str | None = Field(default=None, description="Error message if search failed.")


class PerplexitySearchResponse(BaseModel):
    """Response from perplexity_search tool (AI-synthesized search)."""
    query: str = Field(description="Original search query.")
    answer: str | None = Field(default=None, description="AI-synthesized answer with citations.")
    sources: list[str] | None = Field(
        default=None,
        description="Source URLs cited in the answer.",
    )
    model: str | None = Field(default=None, description="Perplexity model used.")
    steering_message: str | None = Field(
        default=None,
        description="Query guidance message on first call (rate-limited resource).",
    )
    error: str | None = Field(default=None, description="Error message if search failed.")


class YouTubeTranscriptResponse(BaseModel):
    """Response from youtube_transcript tool."""
    video_id: str = Field(description="YouTube video identifier.")
    video_url: str = Field(description="Canonical YouTube URL.")
    title: str | None = Field(default=None, description="Video title if available.")
    transcript_text: str = Field(description="Transcript content in requested format.")
    language: str = Field(description="Language code of transcript.")
    is_translated: bool = Field(default=False, description="Whether transcript was translated.")
    duration_seconds: float | None = Field(default=None, description="Total video duration.")
    transcript_segments: list[dict[str, Any]] | None = Field(
        default=None,
        description="Raw transcript segments if format='json'.",
    )
    error: str | None = Field(default=None, description="Error message if transcript fetch failed.")


class YouTubeSearchResponse(BaseModel):
    """Response from youtube_search tool."""
    query: str = Field(description="Original search query.")
    results: list[WebSearchResult] = Field(
        default_factory=list,
        description="YouTube video results.",
    )
    total_results: int = Field(default=0, description="Total number of video results.")


class SimilarLinkResult(BaseModel):
    """Single related URL returned by Composio Similarlinks."""
    title: str = Field(description="Human-readable result title.")
    link: str = Field(description="Canonical URL for the related page.")
    score: float | None = Field(default=None, description="Provider similarity score.")


class SimilarLinksResponse(BaseModel):
    """Response from Composio Similarlinks."""
    url: str = Field(description="Source URL used to find similar links.")
    results: list[SimilarLinkResult] = Field(default_factory=list)
    total_results: int = Field(default=0, description="Total related links returned.")


class ImageSearchResult(BaseModel):
    """Single image metadata result from Composio Image Search."""
    title: str = Field(description="Image result title.")
    source: str | None = Field(default=None, description="Source site label.")
    page_link: str = Field(description="Page URL where the image appears.")
    original_url: str = Field(description="Original/full-resolution image URL.")
    thumbnail_url: str | None = Field(default=None, description="Thumbnail image URL.")


class ImageSearchResponse(BaseModel):
    """Response from Composio Image Search."""
    query: str = Field(description="Original image search query.")
    results: list[ImageSearchResult] = Field(default_factory=list)
    total_results: int = Field(default=0, description="Total image results returned.")
    page: int = Field(default=0, description="Image search page index.")


class QuickWebSearchCitation(BaseModel):
    """Single citation/source from Composio Quick Web Search."""
    title: str | None = Field(default=None, description="Citation title from the source.")
    url: str | None = Field(default=None, description="URL of the cited source.")
    snippet: str | None = Field(default=None, description="Text snippet from the source.")


class QuickWebSearchResponse(BaseModel):
    """Response from Composio Quick Web Search (COMPOSIO_SEARCH_WEB)."""
    query: str = Field(description="Original search query.")
    answer: str | None = Field(default=None, description="AI-synthesized narrative summary.")
    citations: list[QuickWebSearchCitation] = Field(
        default_factory=list,
        description="Source citations (prioritize these over answer for evidence).",
    )
    total_citations: int = Field(default=0, description="Total citations returned.")


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
PerplexitySearchResultType = PerplexitySearchResponse | ToolErrorResponse
YouTubeTranscriptResultType = YouTubeTranscriptResponse | ToolErrorResponse
YouTubeSearchResultType = YouTubeSearchResponse | ToolErrorResponse
SimilarLinksResultType = SimilarLinksResponse | ToolErrorResponse
ImageSearchResultType = ImageSearchResponse | ToolErrorResponse
QuickWebSearchResultType = QuickWebSearchResponse | ToolErrorResponse
