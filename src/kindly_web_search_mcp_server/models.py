"""Pydantic response models for MCP tool outputs.

P2 Pattern: Typed Pydantic output schemas from Brave/Tavily MCP
- Better agent schema inference through proper type hints
- Provider tracking: providers_used field
- Partial failure handling: warnings field
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .content.models import MarkdownStructure
from .utils.entity import EntityRelation, EntitySpan  # always available (pure python)


class TokenUsage(BaseModel):
    """Standardized token consumption telemetry across AI-backed tools.

    ``prompt_tokens``/``completion_tokens`` match the gemini_search surface;
    Grok's ``input_tokens``/``output_tokens`` map onto these names.
    """

    prompt_tokens: int | None = Field(default=None, description="Input/prompt tokens.")
    completion_tokens: int | None = Field(default=None, description="Output/completion tokens.")
    total_tokens: int | None = Field(default=None, description="Total tokens consumed.")
    cached_input_tokens: int | None = Field(
        default=None, description="Prompt tokens served from cache."
    )
    reasoning_tokens: int | None = Field(
        default=None, description="Internal reasoning/thinking tokens."
    )
    cost_usd: float | None = Field(default=None, description="Estimated API cost in USD.")
    model_used: str | None = Field(default=None, description="Model that handled execution.")
    provider: str | None = Field(default=None, description="Inference backend name.")

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> TokenUsage | None:
        """Build from a summary/LLM payload carrying llm_usage_fields keys."""
        if not isinstance(payload, dict):
            return None
        prompt = payload.get("prompt_tokens", payload.get("input_tokens"))
        completion = payload.get("completion_tokens", payload.get("output_tokens"))
        if completion is None and str(payload.get("summary") or "").strip():
            completion = 0
        prompt_tokens = prompt if isinstance(prompt, int) and not isinstance(prompt, bool) else None
        completion_tokens = (
            completion if isinstance(completion, int) and not isinstance(completion, bool) else None
        )
        total = payload.get("total_tokens")
        total_tokens = total if isinstance(total, int) and not isinstance(total, bool) else None
        if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
            total_tokens = sum(
                value for value in (prompt_tokens, completion_tokens) if value is not None
            )
        backend = payload.get("provider") or payload.get("backend")
        provider_map = {
            "gemini-api": "google",
            "gemini-api-fallback": "google",
            "gemini-batch-api": "google",
            "gemini-per-item-fallback": "google",
            "gemma-fallback": "gemma",
            "gemma-batch-fallback": "gemma",
            "google": "google",
            "gemma": "gemma",
        }
        provider = provider_map.get(str(backend)) if backend else None
        if (
            prompt_tokens is None
            and completion_tokens is None
            and total_tokens is None
            and provider is None
        ):
            return None
        return cls(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model_used=payload.get("model_used") or payload.get("model"),
            provider=provider,
        )


class FilterStats(BaseModel):
    """Outcome counters for the temporal post-filter safety net."""

    dropped_out_of_range: int = Field(
        default=0, description="Dated results outside the requested window."
    )
    dropped_undated: int | None = Field(
        default=None,
        description=(
            "Undated results dropped under the window policy "
            "(None when the policy kept all undated results)."
        ),
    )
    undated_policy: Literal["capability_default", "keep_all", "drop_all"] | None = None


# ============================================================================
# Core Result Types
# ============================================================================


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


class _PublicWebSearchModel(BaseModel):
    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)

    def model_dump_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("exclude_none", True)
        return super().model_dump_json(**kwargs)


class WebSearchNext(_PublicWebSearchModel):
    """Suggested next tool call. ``tool`` plus ``query`` is the call to make."""

    tool: str
    query: dict[str, Any]
    why: str
    confidence: Literal["exact", "high", "medium", "low"]


NextConfidence = Literal["exact", "high", "medium", "low"]


def make_next(
    *,
    tool: str,
    query: dict[str, Any],
    why: str,
    confidence: NextConfidence = "medium",
) -> WebSearchNext:
    return WebSearchNext(tool=tool, query=query, why=why, confidence=confidence)


def fetch_next(
    urls: Sequence[str], *, why: str, confidence: NextConfidence = "medium", limit: int = 3
) -> list[WebSearchNext] | None:
    cleaned = [u for u in urls if isinstance(u, str) and u.strip()][:limit]
    if not cleaned:
        return None
    query = {"urls": cleaned} if len(cleaned) > 1 else {"url": cleaned[0]}
    return [make_next(tool="fetch", query=query, why=why, confidence=confidence)]


class WebSearchHit(_PublicWebSearchModel):
    citation_id: str
    title: str
    url: str
    snippet: str
    domain: str | None = None
    published_date: str | None = None
    freshness: Literal["fresh", "dated", "unknown"]
    consensus: int | None = None
    providers: list[str] | None = None


class WebSearchOverflowHit(_PublicWebSearchModel):
    """Leftover ranked link from the same run. No snippet; fetch if still needed."""

    citation_id: str
    title: str
    url: str


class WebSearchPublicResponse(_PublicWebSearchModel):
    query: str
    results: list[WebSearchHit | WebSearchOverflowHit] = Field(default_factory=list)
    warnings: list[ProviderWarning] | None = None
    next: list[WebSearchNext] | None = None
    remaining: int | None = None
    cursor: str | None = None


PublicStatus = Literal[
    "success",
    "partial",
    "blocked",
    "unsupported",
    "login",
    "paywall",
    "bot",
    "js_shell",
    "error",
]


class FetchError(BaseModel):
    """Typed, actionable error envelope returned by the fetch tool."""

    model_config = ConfigDict(extra="forbid")

    code: str
    category: Literal[
        "validation",
        "auth",
        "rate_limit",
        "upstream",
        "blocked",
        "timeout",
        "internal",
    ]
    message: str
    expected_format: dict[str, Any] | None = None
    resolution: str | None = None
    retryable: bool = False
    http_status: int | None = None
    stage: str | None = None


class _PublicCrawlModel(BaseModel):
    """Crawl response model that omits optional fields from compact output."""

    model_config = ConfigDict(extra="forbid")

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)

    def model_dump_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("exclude_none", True)
        return super().model_dump_json(**kwargs)


class CrawlTargets(_PublicCrawlModel):
    """Optional page, domain, and URL-pattern boundaries for a crawl."""

    css_selector: str | None = Field(
        default=None,
        max_length=2048,
        description="CSS selector that limits extraction to matching page content.",
    )
    allowed_domains: list[str] | None = Field(
        default=None,
        max_length=100,
        description="Domains that seeds and discovered URLs may use, including subdomains.",
    )
    excluded_domains: list[str] | None = Field(
        default=None,
        max_length=100,
        description="Domains to reject even when another crawl boundary would allow them.",
    )
    include_patterns: list[str] | None = Field(
        default=None,
        max_length=100,
        description="URL glob patterns a seed or discovered URL must match.",
    )
    exclude_patterns: list[str] | None = Field(
        default=None,
        max_length=100,
        description="URL glob patterns that reject matching seeds and discovered URLs.",
    )


class CrawlInteraction(_PublicCrawlModel):
    """Bounded browser interaction controls accepted by Crawl4AI."""

    javascript_before_wait: list[str] | None = Field(
        default=None,
        max_length=10,
        description="JavaScript snippets to run before evaluating wait_for.",
    )
    wait_for: str | None = Field(
        default=None,
        max_length=2048,
        description="Crawl4AI wait condition, such as css:.loaded or js:() => window.ready.",
    )
    javascript: list[str] | None = Field(
        default=None,
        max_length=10,
        description="JavaScript snippets to run before content extraction.",
    )
    scan_full_page: bool | None = Field(
        default=None,
        description="Scroll through the page before extraction when true.",
    )

    @model_validator(mode="after")
    def _check_script_limits(self) -> CrawlInteraction:
        scripts = [*(self.javascript_before_wait or ()), *(self.javascript or ())]
        if len(scripts) > 10:
            raise ValueError("interaction accepts at most 10 JavaScript snippets")
        if sum(len(script.encode("utf-8")) for script in scripts) > 32 * 1024:
            raise ValueError("interaction JavaScript is limited to 32768 UTF-8 bytes")
        return self


class CrawlWebRequest(_PublicCrawlModel):
    """Validated request for bounded Crawl4AI browser-backed traversal."""

    urls: list[str] = Field(
        min_length=1,
        max_length=20,
        description="One to twenty absolute HTTP(S) seed URLs.",
    )
    max_depth: int = Field(
        default=0,
        ge=0,
        le=2,
        description="Maximum discovered-link depth; 0 processes only the seed URLs.",
    )
    max_pages: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of attempted pages, including seeds.",
    )
    include_external: bool = Field(
        default=False,
        description="Allow discovered URLs outside the seed sites when true.",
    )
    targets: CrawlTargets | None = Field(
        default=None,
        description="Optional CSS, domain, and URL-pattern boundaries.",
    )
    interaction: CrawlInteraction | None = Field(
        default=None,
        description="Optional bounded browser interaction controls.",
    )
    response_format: Literal["summary", "detailed"] = Field(
        default="summary",
        description="Summary omits page Markdown and links; detailed includes them.",
    )

    @field_validator("urls")
    @classmethod
    def _check_seed_urls(cls, urls: list[str]) -> list[str]:
        for url in urls:
            parsed = urlsplit(url)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                raise ValueError(
                    f"urls contains {url!r}; expected an absolute public HTTP(S) URL, "
                    "for example https://example.com"
                )
        return urls

    @model_validator(mode="after")
    def _check_seed_page_budget(self) -> CrawlWebRequest:
        if self.max_pages < len(self.urls):
            raise ValueError("max_pages cannot be smaller than the number of seed URLs")
        return self


class CrawlWebResult(_PublicCrawlModel):
    """One finalized page artifact from a Crawl4AI traversal."""

    url: str
    status: PublicStatus
    depth: int = 0
    title: str | None = Field(default=None, exclude_if=lambda value: value is None)
    output_path: str | None = Field(default=None, exclude_if=lambda value: value is None)
    word_count: int = 0
    structure: MarkdownStructure = Field(default_factory=MarkdownStructure)
    error: FetchError | None = Field(default=None, exclude_if=lambda value: value is None)
    diagnostics: list[dict[str, Any]] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    content: str | None = Field(default=None, exclude_if=lambda value: value is None)
    links: list[ContentLink] | None = Field(default=None, exclude_if=lambda value: value is None)


class CrawlWebResponse(_PublicCrawlModel):
    """Compact summary or detailed page artifacts from ``crawl_web``."""

    response_format: Literal["summary", "detailed"]
    results: list[CrawlWebResult] = Field(default_factory=list)
    total_requested: int = 0
    total_returned: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    max_depth_reached: int = 0
    capabilities: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    diagnostics: list[dict[str, Any]] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    duration_ms: int = 0


class FetchWindow(BaseModel):
    """Pagination metadata for one fetched content body."""

    model_config = ConfigDict(extra="forbid")

    offset: int = 0
    length: int = 0
    returned_chars: int = 0
    total_chars: int = 0
    has_more: bool = False
    next_offset: int | None = None


class FetchResult(BaseModel):
    """Single URL result returned by the unified fetch tool."""

    model_config = ConfigDict(extra="forbid")

    url: str
    status: PublicStatus
    content: str = ""
    error: FetchError | None = None
    links: list[ContentLink] | None = None
    window: FetchWindow = Field(default_factory=FetchWindow)
    entities: list[EntitySpan] | None = None
    diagnostics: list[dict[str, Any]] | None = None
    output_path: str | None = Field(
        default=None,
        description=(
            "Absolute file path under REPO_ROOT/outputs when processing_mode='index' persisted "
            "the finalized markdown; None in agent mode."
        ),
    )


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
    duration_ms: int = 0


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
    usage: TokenUsage | None = Field(
        default=None,
        description="Canonical usage view of the flat counters above.",
    )
    next: list[WebSearchNext] | None = None


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
    usage: TokenUsage | None = Field(
        default=None,
        description=(
            "Canonical usage view; input_tokens→prompt_tokens, output_tokens→completion_tokens."
        ),
    )
    next: list[WebSearchNext] | None = None


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
    status: Literal["ok", "partial", "error"] | None = None


class SitemapResponse(BaseModel):
    """Response from generate_sitemap tool (Tavily Map payload)."""

    model_config = ConfigDict(extra="ignore")

    base_url: str | None = None
    results: list[str] = Field(default_factory=list)
    related_questions: list[str] | None = None
    images: list[str] | None = None
    error: str | None = None
    next: list[WebSearchNext] | None = None


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
    next: list[WebSearchNext] | None = None


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
    next: list[WebSearchNext] | None = None
