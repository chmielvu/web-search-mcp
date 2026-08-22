"""Pydantic response models for MCP tool outputs.

P2 Pattern: Typed Pydantic output schemas from Brave/Tavily MCP
- Better agent schema inference through proper type hints
- Provider tracking: providers_used field
- Partial failure handling: warnings field
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .entity.models import EntityRelation, EntitySpan  # always available (pure python)


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
    """Warning about a partial failure from a provider.

    Follows the MCP tool error contract: machine-readable ``error_type``,
    an actionable ``action`` hint the agent can act on, ``retry_after`` when
    the provider throttled us, and ``retryable`` to signal transient vs
    permanent failures without re-deriving it from the error text.
    """

    provider: str
    error: str
    error_type: str | None = None
    action: str | None = Field(
        default=None,
        description="Actionable recovery hint for the agent (e.g. wait Ns, verify key).",
    )
    retry_after: float | None = Field(
        default=None,
        description="Seconds to wait before retrying, when the provider rate-limited us.",
    )
    retryable: bool | None = Field(
        default=None,
        description="True when the failure is transient and a retry may succeed.",
    )


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
    total_results: int = Field(
        default=0,
        description=(
            "Ranked-candidate count produced by the search pipeline before "
            "domain/site post-filtering. When domain_boost/domain_block/"
            "site_filters are supplied, len(results) is the authoritative "
            "page size; total_results reflects the pre-filter pool."
        ),
    )
    providers_used: list[str] = Field(
        default_factory=list,
        description="Providers that successfully returned results.",
    )
    warnings: list[ProviderWarning] | None = None
    diagnostics: list[dict[str, Any]] | None = None
    intent: str | None = None
    query_shaping: list[dict[str, Any]] | None = None


class FetchResult(BaseModel):
    """Single URL result returned by the unified fetch tool."""

    input_url: str
    normalized_url: str
    url: str | None = Field(
        default=None,
        description="Resolved URL actually returned to the caller.",
    )
    fetched_url: str | None = None
    status: str = Field(
        description="Fetch status: success, partial, blocked, unsupported, or error."
    )
    source_type: str = Field(
        description="Detected source type, e.g. html, json, rss, csv, pdf, github_issue."
    )
    fetch_backend: str = Field(description="Backend strategy used to retrieve content.")
    origin_backend: str | None = Field(
        default=None,
        description="Backend that originally extracted the content, including on cache hits.",
    )
    cached: bool = False
    page_content: str = ""
    window: dict[str, Any] = Field(default_factory=dict)
    content_format: str = "markdown"
    content_type: str | None = None
    metadata: dict[str, Any] | None = None
    links: list[ContentLink] | None = None
    continuation_notice: str | None = None
    error: dict[str, Any] | None = None
    entities: list[EntitySpan] | None = None
    summary: dict[str, Any] | None = None
    content_quality: str | None = None
    content_word_count: int = 0
    page_char_count: int = 0
    word_count: int = 0
    wall: dict[str, Any] | None = None
    llms_txt: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class FetchResponse(BaseModel):
    """Response from the unified fetch tool."""

    mode: Literal["single", "bulk"]
    results: list[FetchResult] = Field(default_factory=list)
    total_requested: int = 0
    total_returned: int = 0
    total_chars_returned: int = 0
    has_more: bool = False
    cursor: str | None = None
    wave_size: int = 10
    waves_completed: int = 0


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


class GeminiSearchResponse(BaseModel):
    """Response from gemini_search tool (AI-grounded search)."""

    query: str
    mode: str = "single"
    answer: str = ""
    structured_data: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    model_used: str = "gemini-3.1-flash-lite"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    grounding_chunks_count: int = 0
    web_search_queries_count: int = 0
    url_citations: list[dict[str, Any]] = Field(default_factory=list)
    fallback_chain: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    error: str | None = None


class GrokCitation(BaseModel):
    """Single citation from the native xAI Grok Responses search result."""

    url: str
    title: str | None = None
    snippet: str | None = None


class GrokSearchResponse(BaseModel):
    """Response from grok_search with native xAI web/X citations."""

    query: str
    answer: str
    citations: list[GrokCitation] = Field(default_factory=list)
    model: str
    model_used: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    search_queries_used: int = 0
    backend: str = "xai"
    web_search_calls: int = 0
    x_search_calls: int = 0
    sources_used: int = 0
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    error: str | None = None


class YouTubeTranscriptQuality(BaseModel):
    """Quality diagnostics for a normalized YouTube transcript."""

    segment_count: int = 0
    word_count: int = 0
    character_count: int = 0
    duplicate_segments_removed: int = 0
    malformed_segments_removed: int = 0
    truncated: bool = False


class YouTubeTranscriptAnalysis(BaseModel):
    """Always-on GLiNER2 analysis attached to a YouTube transcript."""

    status: Literal["success", "partial", "error"] = "error"
    entities: list[EntitySpan] = Field(default_factory=list)
    relations: list[EntityRelation] = Field(default_factory=list)
    structured_data: dict[str, Any] | None = None
    model_version: str | None = None
    chunk_count: int = 0
    latency_ms: float | None = None
    warnings: list[str] = Field(default_factory=list)


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
    backend_used: str | None = None
    output_format: Literal["text", "timestamped", "json", "markdown"] | None = None
    summary: dict[str, Any] | None = None
    analysis: YouTubeTranscriptAnalysis | None = None
    quality: YouTubeTranscriptQuality | None = None
    error: str | None = None


class YouTubeChannelVideo(BaseModel):
    """Video discovered from a channel uploads playlist."""

    video_id: str
    video_url: str
    title: str = ""
    description: str = ""
    channel_id: str | None = None
    channel_title: str | None = None
    published_at: str | None = None
    position: int | None = None


class YouTubeChannelTranscriptionItem(BaseModel):
    """Per-video result in a channel transcription task."""

    video: YouTubeChannelVideo
    status: Literal["success", "cached", "failed", "skipped"] = "failed"
    transcript: YouTubeTranscriptResponse | None = None
    error: str | None = None


class YouTubeChannelTranscriptionResponse(BaseModel):
    """Aggregate result from a channel transcription task."""

    channel_id: str
    total_videos: int
    completed_videos: int = 0
    failed_videos: int = 0
    items: list[YouTubeChannelTranscriptionItem] = Field(default_factory=list)
    next_page_token: str | None = None
    quota: dict[str, Any] | None = None
    error: str | None = None


class YouTubeSearchResponse(BaseModel):
    """Response from youtube_search tool."""

    query: str
    results: list[WebSearchResult] = Field(default_factory=list)
    total_results: int = 0
    search_backend: str | None = None  # "api" or "searxng"


class SitemapResponse(BaseModel):
    """Response from generate_sitemap tool (Tavily Map payload)."""

    model_config = ConfigDict(extra="ignore")

    base_url: str | None = None
    results: list[str] = Field(default_factory=list)
    related_questions: list[str] | None = None
    images: list[str] | None = None
    error: str | None = None


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
    source: str = Field(
        description="Provider: arxiv, semanticscholar, openalex, crossref, pubmed, core, radon, bn, pbn, polona, dlibra, rds, europeana."
    )
    source_id: str
    external_ids: dict[str, str] | None = None
    fields_of_study: list[str] | None = None
    is_open_access: bool | None = None
    score: float | None = None
    source_type: Literal["general", "polish", "archive"] = "general"
    date_descriptive: str | None = None
    highlights: list[str] | None = None
    fulltext_url: str | None = None


class AcademicSearchResponse(BaseModel):
    """Response from academic_search tool."""

    query: str
    results: list[AcademicPaper] = Field(default_factory=list)
    total_results: int = 0
    sources_used: list[str] = Field(default_factory=list)
    source_types_used: list[str] = Field(default_factory=list)
    warnings: list[ProviderWarning] | None = None
