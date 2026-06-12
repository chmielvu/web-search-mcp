from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ResearchDepth = Literal["quick", "normal", "deep"]


class SearchCandidate(BaseModel):
    title: str
    link: str
    snippet: str
    domain: str | None = None
    published_date: str | None = None
    provider: str | None = None
    score: float | None = None
    raw_score: float | None = None


class AgenticResearchRequest(BaseModel):
    query: str = Field(description="The main research question or search brief.")
    research_goal: str | None = Field(
        default=None,
        description="Optional description of why the research is being done.",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional MCP session identifier for Langfuse trace grouping.",
    )
    depth: ResearchDepth = Field(default="normal")


class ResearchSource(BaseModel):
    title: str | None = None
    url: str
    snippet: str | None = None
    tool: str
    domain: str | None = None
    score: float | None = None
    kind: str = "search"


class ResearchGraphSummary(BaseModel):
    node_count: int = 0
    edge_count: int = 0
    tool_count: int = 0
    url_count: int = 0
    domain_count: int = 0
    source_urls: list[str] = Field(default_factory=list)
    fetched_urls: list[str] = Field(default_factory=list)
    tool_calls: dict[str, int] = Field(default_factory=dict)
    potential_conflicts: list[str] = Field(default_factory=list)


class AgenticResearchResult(BaseModel):
    query: str
    research_goal: str | None = None
    depth: ResearchDepth = "normal"
    model: str
    answer: str
    sources: list[ResearchSource] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    tool_trace: list[str] = Field(default_factory=list)
    knowledge_graph_summary: ResearchGraphSummary = Field(
        default_factory=ResearchGraphSummary
    )
    run_limit: int = 0
    duration_seconds: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    extra: dict[str, Any] | None = None


class SearchInput(BaseModel):
    query: str = Field(description="Search query.")
    num_results: int = Field(default=5, ge=1, le=20)


class SimilarLinksInput(BaseModel):
    url: str = Field(description="Known good URL to expand from.")
    num_results: int = Field(default=5, ge=1, le=20)
    search_type: str = Field(default="neural")
    category: str | None = None
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None


class ImageSearchInput(BaseModel):
    query: str = Field(description="Image search query.")
    num_results: int = Field(default=10, ge=1, le=100)
    page: int = Field(default=0, ge=0)


class GetContentInput(BaseModel):
    url: str = Field(description="Single URL to fetch.")
    char_offset: int = Field(default=0, ge=0)
    char_length: int = Field(default=12000, ge=1)
    include_metadata: bool = True
    include_links: bool = False
    max_links: int = Field(default=25, ge=1, le=200)
    strip_selectors: str | None = None
    timeout_seconds: float = Field(default=120.0, ge=1.0)


class BatchGetContentInput(BaseModel):
    urls: list[str] | None = None
    cursor: str | None = None
    max_concurrency: int = Field(default=4, ge=1, le=8)
    per_item_char_length: int = Field(default=12000, ge=1)
    total_char_budget: int = Field(default=120000, ge=1)
    per_url_timeout_seconds: float = Field(default=120.0, ge=1.0)
    include_metadata: bool = True
    include_links: bool = False
    max_links: int = Field(default=25, ge=1, le=200)
    strip_selectors: str | None = None


class DiscoverLinksInput(BaseModel):
    url: str = Field(description="Page or sitemap URL to inspect.")
    max_links: int = Field(default=100, ge=1, le=1000)
    include_external: bool = True
    same_domain_only: bool = False
    strip_selectors: str | None = None


class RerankCandidateInput(BaseModel):
    title: str
    link: str
    snippet: str
    domain: str | None = None
    published_date: str | None = None
    provider: str | None = None
    score: float | None = None
    raw_score: float | None = None


class RerankCandidatesInput(BaseModel):
    query: str
    candidates: list[RerankCandidateInput]
    top_k: int = Field(default=10, ge=1, le=20)


class AcademicSearchInput(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=20)
    sources: list[str] | None = None
    year_from: int | None = None
    year_to: int | None = None
    fields_of_study: list[str] | None = None
    venue: str | None = None
    open_access_only: bool = False
    sort: str = Field(default="relevance")


class FinalAnswerInput(BaseModel):
    """Structured output the agent can call when it decides research is complete.

    Always available to the ReAct agent (no enable flag). Using this gives stronger
    guarantees on citation and source lists than pure text extraction from the last AIMessage.
    The runner detects ToolMessage(name="final_answer") and prefers the structured payload.
    """

    answer: str = Field(
        description="Final synthesized answer. May include [N] citations."
    )
    sources: list[dict] = Field(
        description="List of sources used: each with at least 'url' and optionally 'title', 'key_finding'."
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Agent's self-reported confidence in the answer (0-1).",
    )
    gaps: str = Field(
        default="",
        description="Any remaining uncertainties, missing information, or caveats.",
    )
