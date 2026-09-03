from __future__ import annotations

import re
import shlex
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field


Confidence = Literal["high", "medium", "low"]
McpProfile = Literal["regular", "full"]
PromptName = Literal[
    "web_search_workflow",
    "query_refinement",
    "research_methodology",
]
PromptDepth = Literal["quick", "medium", "deep"]
PromptFocus = Literal["code", "academic", "news", "general"]


class _PromptArguments(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}


class WebSearchWorkflowPromptArguments(_PromptArguments):
    query: str
    num_results: int = Field(default=5, ge=1, le=10)
    depth: PromptDepth = "medium"
    focus: PromptFocus = "general"


class QueryRefinementPromptArguments(_PromptArguments):
    original_query: str
    failed_attempts: list[str] = Field(default_factory=list)
    reason: str | None = None


class ResearchMethodologyPromptArguments(_PromptArguments):
    pass


PromptArguments: TypeAlias = (
    WebSearchWorkflowPromptArguments
    | QueryRefinementPromptArguments
    | ResearchMethodologyPromptArguments
)


class CommandRoute(BaseModel):
    """One deterministic CLI/MCP route selected from a user task."""

    command: str
    mcp_tool: str | None = None
    required_profile: McpProfile | None = None
    intent: str
    confidence: Confidence
    reason: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    workflow: list[str] = Field(default_factory=list)
    prompt_name: PromptName | None = None
    prompt_arguments: PromptArguments | None = None


class CommandRecommendation(BaseModel):
    """Recommendation-only response; commands are never executed here."""

    task: str
    intent: str
    confidence: Confidence
    recommended_command: str
    recommended_route: CommandRoute
    fallback_commands: list[str] = Field(default_factory=list)
    fallback_routes: list[CommandRoute] = Field(default_factory=list)
    reason: str
    decomposition_required: bool = False
    orchestration_strategy: str | None = None
    decomposition_rules: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)


_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_GITHUB_RE = re.compile(
    r"(?:https?://(?:www\.)?github\.com/|github\.com/)?"
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)

_URL_TRAILING_CHARS = ".,;:!?)]}，。、；：！？）》】"


def _clean_url(value: str) -> str:
    return value.rstrip(_URL_TRAILING_CHARS)


def _extract_urls(task: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(task):
        url = _clean_url(match.group(0))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _extract_github_repo(task: str) -> str:
    lowered = task.casefold()
    if "github" not in lowered and "repo" not in lowered and "repository" not in lowered:
        return ""
    for match in _GITHUB_RE.finditer(task):
        repo = match.group("repo").rstrip(".")
        if repo.casefold() in {"http", "https"}:
            continue
        return repo
    return ""


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value in text for value in values)


def _is_complex_task(task: str) -> bool:
    lowered = task.casefold()
    score = 0
    if len(task) >= 220:
        score += 1
    if lowered.count(" and ") + lowered.count(" or ") >= 3:
        score += 1
    if any(marker in lowered for marker in ("(a)", "(b)", "(c)", "first", "second", "third")):
        score += 1
    if sum(
        marker in lowered
        for marker in (
            "deep research",
            "deep dive",
            "compare",
            "comparison",
            "architecture",
            "trade-off",
            "primary source",
            "counterevidence",
            "whether",
            "investigate",
            "analyze",
        )
    ) >= 2:
        score += 1
    return score >= 2


def _render_command(arguments: list[str]) -> str:
    return shlex.join(["uv", "run", "web-search-cli", *arguments])


def _web_search_prompt_arguments(
    query: str,
    *,
    num_results: int,
    depth: PromptDepth,
    focus: PromptFocus = "general",
) -> WebSearchWorkflowPromptArguments:
    return WebSearchWorkflowPromptArguments(
        query=query,
        num_results=num_results,
        depth=depth,
        focus=focus,
    )


def _route(
    arguments: list[str],
    *,
    intent: str,
    confidence: Confidence,
    reason: str,
    mcp_tool: str | None,
    required_profile: McpProfile | None,
    structured_arguments: dict[str, Any],
    workflow: list[str] | None = None,
    prompt_name: PromptName | None = None,
    prompt_arguments: PromptArguments | None = None,
) -> CommandRoute:
    return CommandRoute(
        command=_render_command(arguments),
        mcp_tool=mcp_tool,
        required_profile=required_profile,
        intent=intent,
        confidence=confidence,
        reason=reason,
        arguments=structured_arguments,
        workflow=workflow or [],
        prompt_name=prompt_name,
        prompt_arguments=prompt_arguments,
    )


def _route_for_task(task: str) -> CommandRoute:
    lowered = task.casefold()
    urls = _extract_urls(task)
    github_repo = _extract_github_repo(task)

    if len(urls) >= 3:
        arguments: list[str] = ["content", "fetch"]
        for url in urls:
            arguments.extend(("--url", url))
        return _route(
            arguments,
            intent="multi_url_read",
            confidence="high",
            reason="The task contains multiple known URLs; fetch preserves ordered per-source results and bounded continuation.",
            mcp_tool="fetch",
            required_profile="regular",
            structured_arguments={"urls": urls},
            workflow=["content fetch", "inspect has_more/cursor"],
        )

    if urls:
        url = urls[0]
        if ("youtube.com" in url or "youtu.be" in url) and _contains_any(
            lowered, ("transcript", "caption", "subtitle", "字幕", "字幕稿")
        ):
            return _route(
                ["youtube", "transcript", "--video-id-or-url", url],
                intent="video_transcript",
                confidence="high",
                reason="A known YouTube URL and transcript intent map directly to the transcript command.",
                mcp_tool="youtube_transcript",
                required_profile="regular",
                structured_arguments={"--video-id-or-url": url},
                workflow=["youtube transcript"],
            )
        if _contains_any(
            lowered,
            ("map", "site map", "sitemap", "site structure", "navigation", "站点结构", "导航"),
        ):
            return _route(
                ["sitemap", "generate", "--url", url],
                intent="site_map",
                confidence="high",
                reason="A known URL with site-structure intent maps to Tavily Map through the current sitemap command.",
                mcp_tool="generate_sitemap",
                required_profile="regular",
                structured_arguments={"--url": url},
                workflow=["sitemap generate", "content batch on selected URLs"],
            )
        if _contains_any(lowered, ("link", "outbound", "link graph", "链接", "链接图")):
            return _route(
                ["links", "discover", "--url", url],
                intent="link_discovery",
                confidence="high",
                reason="A known URL with link-discovery intent maps to the current links command.",
                mcp_tool="discover_links",
                required_profile="regular",
                structured_arguments={"--url": url},
                workflow=["links discover", "content get on promising URLs"],
            )
        summary = _contains_any(lowered, ("summarize", "summary", "概述", "摘要", "总结"))
        arguments = ["content", "fetch", "--url", url]
        structured: dict[str, Any] = {"url": url}
        if summary:
            arguments.append("--ai-summary")
            structured["ai_summary"] = True
        return _route(
            arguments,
            intent="known_url_read",
            confidence="high",
            reason="A known URL should be read directly before broader discovery.",
            mcp_tool="fetch",
            required_profile="regular",
            structured_arguments=structured,
            workflow=["content fetch", "paginate with offset when the result window has_more"],
        )

    if github_repo:
        if _contains_any(
            lowered,
            ("release", "releases", "stars", "metadata", "repo info", "repository info", "发布", "仓库信息"),
        ):
            return _route(
                ["search", "code", "--query", github_repo, "--repository", github_repo, "--mode", "discovery"],
                intent="github_repository_discovery",
                confidence="high",
                reason="The task asks about a known GitHub repository rather than general web discovery.",
                mcp_tool="code_search",
                required_profile="regular",
                structured_arguments={
                    "--query": github_repo,
                    "--repository": github_repo,
                    "--mode": "discovery",
                },
                workflow=["search code --mode discovery", "code_fetch with repository + query to search its snapshot"],
            )
        mode = "docs" if _contains_any(lowered, ("readme", "documentation", "docs", "api reference", "文档")) else "code"
        arguments = ["search", "code", "--query", task, "--repository", github_repo, "--mode", mode]
        if mode == "code" or _contains_any(lowered, ("implementation", "source", "源码", "实现")):
            arguments.append("--deep")
        return _route(
            arguments,
            intent="github_code_or_docs",
            confidence="high",
            reason="A known GitHub repository plus implementation or documentation intent maps to code search.",
            mcp_tool="code_search",
            required_profile="regular",
            structured_arguments={
                "--query": task,
                "--repository": github_repo,
                "--mode": mode,
                "--deep": mode == "code",
            },
            workflow=["search code", "code_fetch with repository + query to search the full snapshot"],
        )

    if _contains_any(lowered, ("paper", "papers", "arxiv", "scholarly", "academic", "benchmark", "论文", "学术")):
        return _route(
            ["search", "academic", "--query", task],
            intent="academic_search",
            confidence="high",
            reason="Paper, benchmark, and scholarly language maps to the dedicated academic search command.",
            mcp_tool="academic_search",
            required_profile="regular",
            structured_arguments={"--query": task},
            workflow=["search academic", "content get on selected papers", "cross-check independent sources"],
        )

    if _contains_any(lowered, ("analytics", "latency", "provider performance", "error count", "funnel", "分析数据", "指标")):
        return _route(
            ["analytics", "query", "--question", task],
            intent="analytics_query",
            confidence="medium",
            reason="The task asks about local operational or search analytics rather than external web content.",
            mcp_tool=None,
            required_profile=None,
            structured_arguments={"--question": task},
            workflow=["analytics query", "analytics report for deterministic follow-up"],
        )

    if _contains_any(lowered, ("youtube", "video", "videos", "视频", "教程视频")):
        return _route(
            ["youtube", "search", "--query", task],
            intent="video_search",
            confidence="medium",
            reason="Video-oriented discovery maps to YouTube search before transcript extraction.",
            mcp_tool="youtube_search",
            required_profile="regular",
            structured_arguments={"--query": task},
            workflow=["youtube search", "youtube transcript on the best result"],
        )

    if _contains_any(
        lowered,
        ("function", "class", "stack trace", "error message", "implementation", "source code", "code example", "bug", "api usage", "代码", "源码", "报错"),
    ):
        return _route(
            ["search", "code", "--query", task, "--deep"],
            intent="code_search",
            confidence="medium",
            reason="Implementation, error, and API-language is best served by the typed code-search surface.",
            mcp_tool="code_search",
            required_profile="regular",
            structured_arguments={"--query": task, "--deep": True},
            workflow=["search code", "code_fetch with repository + query to search the full snapshot"],
        )

    if _contains_any(lowered, ("quick", "fast", "reconnaissance", "map the landscape", "快速", "速览")):
        return _route(
            ["search", "quick", "--query", task, "--objective", task],
            intent="quick_reconnaissance",
            confidence="medium",
            reason="The task asks for quick orientation rather than a full multi-provider investigation.",
            mcp_tool="quick_web_search",
            required_profile="regular",
            structured_arguments={"--query": task, "--objective": task},
            workflow=["search quick", "search web after terminology or gaps are found"],
            prompt_name="web_search_workflow",
            prompt_arguments=_web_search_prompt_arguments(
                task, num_results=3, depth="quick"
            ),
        )

    if _contains_any(lowered, ("what is", "who is", "latest", "current", "news", "summarize", "summary", "what happened", "最新", "当前", "总结")):
        return _route(
            ["ai", "gemini", "--query", task, "--research-goal", task],
            intent="grounded_answer",
            confidence="medium",
            reason="A factual, current, or synthesis request maps to the grounded Gemini route before deep discovery.",
            mcp_tool="gemini_search",
            required_profile="regular",
            structured_arguments={"--query": task, "--research-goal": task},
            workflow=["ai gemini", "content get on sources when deeper verification is needed"],
            prompt_name="web_search_workflow",
            prompt_arguments=_web_search_prompt_arguments(
                task, num_results=3, depth="quick"
            ),
        )

    return _route(
        ["search", "web", "--query", task, "--research-goal", task],
        intent="web_discovery",
        confidence="low",
        reason="No more specific intent was detected; use the general multi-provider discovery route.",
        mcp_tool="web_search",
        required_profile="regular",
        structured_arguments={"--query": task, "--research-goal": task},
        workflow=["search web", "content batch on the strongest sources", "iterate on evidence gaps"],
        prompt_name="web_search_workflow",
        prompt_arguments=_web_search_prompt_arguments(
            task, num_results=5, depth="medium"
        ),
    )


def build_command_recommendation(task: str) -> CommandRecommendation:
    """Recommend existing CLI/MCP routes from a natural-language task.

    This function is deterministic and recommendation-only. It does not invoke
    providers, execute shell commands, or infer credentials.
    """
    cleaned = " ".join((task or "").split())
    if not cleaned:
        raise ValueError("task must be a non-blank natural-language request")

    primary = _route_for_task(cleaned)
    complex_task = _is_complex_task(cleaned)
    if complex_task and primary.intent == "web_discovery":
        primary = primary.model_copy(
            update={
                "prompt_name": "research_methodology",
                "prompt_arguments": ResearchMethodologyPromptArguments(),
            }
        )

    fallback_routes: list[CommandRoute] = []

    if primary.intent in {"known_url_read", "multi_url_read", "site_map", "link_discovery", "video_transcript"}:
        fallback_routes.append(
            _route(
                ["search", "web", "--query", cleaned, "--research-goal", cleaned],
                intent="web_discovery",
                confidence="low",
                reason="If the direct URL workflow is insufficient, discover corroborating sources.",
                mcp_tool="web_search",
                required_profile="regular",
                structured_arguments={"--query": cleaned, "--research-goal": cleaned},
                workflow=["search web", "content get or content batch"],
                prompt_name="web_search_workflow",
                prompt_arguments=_web_search_prompt_arguments(
                    cleaned, num_results=5, depth="medium"
                ),
            )
        )
    elif primary.intent == "quick_reconnaissance":
        fallback_routes.append(
            _route(
                ["ai", "gemini", "--query", cleaned, "--research-goal", cleaned],
                intent="grounded_answer",
                confidence="medium",
                reason="Use grounded synthesis when quick reconnaissance is not enough.",
                mcp_tool="gemini_search",
                required_profile="regular",
                structured_arguments={"--query": cleaned, "--research-goal": cleaned},
                workflow=["ai gemini", "content get on sources"],
                prompt_name="web_search_workflow",
                prompt_arguments=_web_search_prompt_arguments(
                    cleaned, num_results=3, depth="quick"
                ),
            )
        )
    elif primary.intent != "web_discovery":
        fallback_routes.append(
            _route(
                ["search", "web", "--query", cleaned, "--research-goal", cleaned],
                intent="web_discovery",
                confidence="low",
                reason="Use general multi-provider discovery when the specialized route has sparse coverage.",
                mcp_tool="web_search",
                required_profile="regular",
                structured_arguments={"--query": cleaned, "--research-goal": cleaned},
                workflow=["search web", "content batch on selected sources"],
                prompt_name="web_search_workflow",
                prompt_arguments=_web_search_prompt_arguments(
                    cleaned, num_results=5, depth="medium"
                ),
            )
        )

    if complex_task:
        decomposition_rules = [
            "Split the task into 2-4 independent sub-questions before executing a route.",
            "Use one recommendation per sub-question instead of one command mixing unrelated intents.",
            "Prefer primary or official sources, then fetch the strongest evidence before synthesis.",
        ]
        orchestration_strategy = "split_then_route"
    else:
        decomposition_rules = []
        orchestration_strategy = None

    safety_notes = [
        "Recommendation only: no command or provider call is executed.",
        "Review the quoted command and structured arguments before passing them to a shell.",
    ]

    return CommandRecommendation(
        task=cleaned,
        intent=primary.intent,
        confidence="medium" if complex_task and primary.confidence == "low" else primary.confidence,
        recommended_command=primary.command,
        recommended_route=primary,
        fallback_commands=[route.command for route in fallback_routes],
        fallback_routes=fallback_routes,
        reason=primary.reason,
        decomposition_required=complex_task,
        orchestration_strategy=orchestration_strategy,
        decomposition_rules=decomposition_rules,
        safety_notes=safety_notes,
    )


__all__ = [
    "CommandRecommendation",
    "CommandRoute",
    "PromptArguments",
    "PromptDepth",
    "PromptFocus",
    "PromptName",
    "QueryRefinementPromptArguments",
    "ResearchMethodologyPromptArguments",
    "WebSearchWorkflowPromptArguments",
    "build_command_recommendation",
]
