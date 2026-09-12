"""Importable v2 prototype for Parallel AI, Context7/DeepWiki, and YouTube search.

The module has no CLI. Call one of the three public runners with its Pydantic request
model, or pass that model to ``run_v2``. Provider-specific payloads are normalized at
the boundary so callers receive one predictable Pydantic response shape.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
from collections.abc import Awaitable, Callable, Iterator, Sequence
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

load_dotenv(override=True)

SCHEMA_VERSION = "2.0-prototype"
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_CHARS = 12_000
DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_QUERIES = 5
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


_NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Model(BaseModel):
    """Strict local contract base; remote response models opt into ignored extras."""

    model_config = ConfigDict(extra="forbid")


class _Request(_Model):
    """Shared limits for all request modes."""

    max_results: int = Field(DEFAULT_MAX_RESULTS, gt=0, le=50)
    max_chars_total: int = Field(DEFAULT_MAX_CHARS, gt=0, le=100_000)
    timeout_seconds: float = Field(DEFAULT_TIMEOUT_SECONDS, gt=0, le=600)


class VersatileRequest(_Request):
    """Request for Parallel advanced multi-query search."""

    queries: list[_NonEmptyStr] = Field(min_length=1, max_length=MAX_QUERIES)
    objective: _NonEmptyStr = "Find relevant, current sources for the requested topic."


class DocumentationRequest(_Request):
    """Request for the Context7 and DeepWiki documentation pair."""

    repo_url: _NonEmptyStr
    question: _NonEmptyStr
    context7_library_id: _NonEmptyStr | None = None


class YouTubeRequest(_Request):
    """Request for the YouTube provider cascade."""

    query: _NonEmptyStr


class ErrorPayload(_Model):
    """Typed provider error with enough context for an agent to recover."""

    kind: str
    message: str
    hint: str
    context: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class PrototypeError(RuntimeError):
    """Exception carrying a structured provider error."""

    def __init__(
        self,
        kind: str,
        message: str,
        hint: str,
        *,
        context: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.payload = ErrorPayload(
            kind=kind,
            message=message,
            hint=hint,
            context=context or {},
            retryable=retryable,
        )


class ProviderWarning(_Model):
    """A provider failure retained beside a partial successful response."""

    provider: str
    kind: str
    message: str
    hint: str
    retryable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class ProviderUsage(_Model):
    """Small common record of external requests made by a mode."""

    provider: str
    requests: int
    endpoint: str
    details: dict[str, Any] = Field(default_factory=dict)


class WebResult(_Model):
    """Normalized Parallel search result."""

    kind: Literal["web_result"] = "web_result"
    provider: Literal["parallel"] = "parallel"
    title: str | None = None
    url: str | None = None
    snippet: str = ""
    publish_date: str | None = None


class DocumentationInsight(_Model):
    """Context7 reference or code example retained with provenance."""

    kind: Literal["reference", "code"]
    title: str
    content: str
    language: str | None = None
    source: str | None = None


class DocumentationResult(_Model):
    """Merged documentation answer with provider evidence."""

    kind: Literal["documentation"] = "documentation"
    provider: Literal["documentation_merge"] = "documentation_merge"
    repository: str
    question: str
    answer: str
    deepwiki_prose: str | None = None
    context7_intelligence: list[DocumentationInsight] = Field(default_factory=list)


class YouTubeVideo(_Model):
    """Provider-neutral YouTube video metadata."""

    kind: Literal["youtube_video"] = "youtube_video"
    provider: Literal["youtube_api", "searxng_youtube", "youtube_html"]
    title: str
    url: str
    snippet: str = ""
    video_id: str | None = None
    channel_id: str | None = None
    channel_title: str | None = None
    published_at: str | None = None
    duration: str | None = None
    views: str | None = None


SearchResult = WebResult | DocumentationResult | YouTubeVideo
SearchItem = WebResult | YouTubeVideo

YouTubeProviderFn = Callable[
    [YouTubeRequest, httpx.AsyncClient],
    Awaitable[tuple[list[YouTubeVideo], ProviderUsage]],
]


class SearchResponse(_Model):
    """Common response envelope for every mode."""

    schema_version: str = SCHEMA_VERSION
    mode: Literal["versatile", "documentation", "youtube"]
    status: Literal["ok", "partial", "no_hit"]
    attempted_providers: list[str]
    providers: list[str]
    results: list[SearchResult] = Field(default_factory=list)
    total_results: int = 0
    truncated: bool = False
    omitted_results: int = 0
    warnings: list[ProviderWarning] = Field(default_factory=list)
    usage: list[ProviderUsage] = Field(default_factory=list)


class Repository(_Model):
    """Parsed GitHub identity used to form both provider requests."""

    url: str
    owner: str
    name: str


class _Context7Search(BaseModel):
    """Fields consumed from Context7's documented library-search response."""

    model_config = ConfigDict(extra="ignore")

    results: list[dict[str, Any]] = Field(default_factory=list)


class _Context7Context(BaseModel):
    """Fields consumed from Context7's documented JSON context response."""

    model_config = ConfigDict(extra="ignore")

    infoSnippets: list[dict[str, Any]] = Field(default_factory=list)
    codeSnippets: list[dict[str, Any]] = Field(default_factory=list)


def _text(value: Any) -> str:
    """Return stripped string text, or an empty string for other values."""
    return value.strip() if isinstance(value, str) else ""


def _text_or_none(value: Any) -> str | None:
    """Return stripped string text, or None when no text is available."""
    return _text(value) or None


def _value(value: Any, key: str, default: Any = None) -> Any:
    """Read a field from either an SDK object or a JSON mapping."""
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _field_text_or_none(value: Any, key: str) -> str | None:
    """Read one field and return normalized text or None."""
    return _text_or_none(_value(value, key))


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    """Truncate text to a character budget."""
    if len(value) <= limit:
        return value, False
    return f"{value[: max(0, limit - 1)]}…", True


def _repository(repo_url: str) -> Repository:
    """Validate and parse the GitHub URL required by DeepWiki."""
    value = _text(repo_url)
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").casefold() not in {"github.com", "www.github.com"}
        or len(parts) < 2
    ):
        raise PrototypeError(
            "invalid_repository",
            "Documentation mode requires a public GitHub repository URL.",
            "Use https://github.com/owner/repository.",
            context={"repo_url": value},
        )
    owner, name, *_ = parts
    return Repository(url=value.rstrip("/"), owner=owner, name=name.removesuffix(".git"))


def _warning(provider: str, error: BaseException) -> ProviderWarning:
    """Convert a provider exception into response warning data."""
    if isinstance(error, PrototypeError):
        payload = error.payload
        return ProviderWarning(
            provider=provider,
            kind=payload.kind,
            message=payload.message,
            hint=payload.hint,
            retryable=payload.retryable,
            context=payload.context,
        )
    return ProviderWarning(
        provider=provider,
        kind="provider_error",
        message=f"{type(error).__name__}: {_text(str(error))[:300]}",
        hint="Inspect provider connectivity and retry.",
        retryable=True,
    )


async def _attempt_youtube_provider(
    name: str,
    provider: YouTubeProviderFn,
    request: YouTubeRequest,
    client: httpx.AsyncClient,
    warnings: list[ProviderWarning],
    usage: list[ProviderUsage],
) -> list[YouTubeVideo]:
    """Try one YouTube provider and retain failures as warnings."""
    try:
        videos, item = await provider(request, client)
    except Exception as exc:
        warnings.append(_warning(name, exc))
        return []
    usage.append(item)
    return videos


def _response(
    mode: Literal["versatile", "documentation", "youtube"],
    *,
    attempted_providers: list[str],
    providers: list[str],
    results: Sequence[SearchResult],
    warnings: Sequence[ProviderWarning] = (),
    usage: Sequence[ProviderUsage] = (),
    truncated: bool = False,
    omitted_results: int = 0,
) -> SearchResponse:
    """Construct the shared response envelope."""
    result_list = list(results)
    warning_list = list(warnings)
    if warning_list:
        status = "partial"
    elif result_list:
        status = "ok"
    else:
        status = "no_hit"
    return SearchResponse(
        mode=mode,
        status=status,
        attempted_providers=attempted_providers,
        providers=providers,
        results=result_list,
        total_results=len(result_list),
        truncated=truncated,
        omitted_results=omitted_results,
        warnings=warning_list,
        usage=list(usage),
    )


def _bound_search_items(
    items: Sequence[SearchItem], max_chars: int
) -> tuple[list[SearchItem], bool, int]:
    """Bound result snippets without changing provider order or object shape."""
    remaining = max_chars
    bounded: list[SearchItem] = []

    for index, item in enumerate(items):
        if remaining <= 0:
            return bounded, True, len(items) - index
        if len(item.snippet) > remaining:
            snippet, _ = _truncate(item.snippet, remaining)
            bounded.append(item.model_copy(update={"snippet": snippet}))
            return bounded, True, len(items) - index - 1
        bounded.append(item)
        remaining -= len(item.snippet)

    return bounded, False, 0


async def _context7_get(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    """Call a Context7 endpoint and validate its JSON object response."""
    headers = {"Accept": "application/json", "User-Agent": "quick-web-search-v2-prototype"}
    token = os.environ.get("CONTEXT7_API_KEY", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = await client.get(
            f"https://context7.com/api{path}",
            params=params,
            headers=headers,
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise PrototypeError(
            "context7_network_error",
            f"Context7 request failed: {type(exc).__name__}.",
            "Check Context7 connectivity and retry.",
            context={"path": path},
            retryable=True,
        ) from exc
    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            detail = (
                _text(body.get("message") or body.get("error")) if isinstance(body, dict) else ""
            )
        except ValueError:
            pass
        raise PrototypeError(
            "context7_http_error",
            f"Context7 returned HTTP {response.status_code}{f': {detail}' if detail else ''}.",
            "Check the library ID and query; retry transient failures.",
            context={"path": path, "status": response.status_code},
            retryable=response.status_code in {429, 500, 502, 503, 504},
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise PrototypeError(
            "context7_invalid_response",
            "Context7 returned invalid JSON.",
            "Retry the request and inspect the Context7 response.",
            context={"path": path},
        ) from exc
    if not isinstance(data, dict):
        raise PrototypeError(
            "context7_invalid_response",
            "Context7 returned a non-object JSON response.",
            "Retry the request and inspect the Context7 response.",
            context={"path": path},
        )
    return data


async def _context7(
    request: DocumentationRequest, repo: Repository, client: httpx.AsyncClient
) -> list[DocumentationInsight]:
    """Resolve a library and retrieve its ranked Context7 documentation."""
    library_hint = request.context7_library_id or f"/{repo.owner}/{repo.name}"
    search_data = await _context7_get(
        client,
        "/v2/libs/search",
        params={"libraryName": library_hint.lstrip("/"), "query": request.question},
        timeout_seconds=request.timeout_seconds,
    )
    search = _Context7Search.model_validate(search_data)
    selected_id = library_hint
    for item in search.results:
        item_id = _text(item.get("id"))
        if item_id:
            selected_id = item_id
            break
    data = await _context7_get(
        client,
        "/v2/context",
        params={"libraryId": selected_id, "query": request.question, "type": "json"},
        timeout_seconds=request.timeout_seconds,
    )
    context = _Context7Context.model_validate(data)
    insights: list[DocumentationInsight] = []
    for item in context.infoSnippets:
        content = _text(item.get("content"))
        if content:
            insights.append(
                DocumentationInsight(
                    kind="reference",
                    title=_text(item.get("breadcrumb")) or _text(item.get("pageId")) or selected_id,
                    content=content,
                    source=_text(item.get("pageId")) or None,
                )
            )
    for group in context.codeSnippets:
        title = _text(group.get("codeTitle")) or _text(group.get("pageTitle")) or selected_id
        source = _text(group.get("codeId")) or None
        default_language = _text(group.get("codeLanguage")) or None
        code_list = group.get("codeList", [])
        if not isinstance(code_list, list):
            continue
        for example in code_list:
            if not isinstance(example, dict):
                continue
            code = _text(example.get("code"))
            if code:
                insights.append(
                    DocumentationInsight(
                        kind="code",
                        title=title,
                        content=code,
                        language=_text(example.get("language")) or default_language,
                        source=source,
                    )
                )
    return _deduplicate(insights)


def _mcp_text(result: Any) -> str:
    """Extract prose from typed or mapping-shaped MCP results."""
    content = _value(result, "content", [])
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        prose = "\n".join(text for item in content if (text := _text(_value(item, "text"))))
        if prose:
            return prose
    structured = _value(result, "structuredContent")
    if isinstance(structured, dict):
        for key in ("answer", "content", "text", "response"):
            if prose := _text(structured.get(key)):
                return prose
    return ""


async def _deepwiki(request: DocumentationRequest, repo: Repository) -> str:
    """Ask DeepWiki's ``ask_question`` tool over Streamable HTTP."""
    endpoint = "https://mcp.deepwiki.com/mcp"
    repo_name = f"{repo.owner}/{repo.name}"
    try:
        async with asyncio.timeout(request.timeout_seconds):
            async with streamable_http_client(endpoint) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "ask_question",
                        arguments={"repoName": repo_name, "question": request.question},
                    )
    except TimeoutError as exc:
        raise PrototypeError(
            "deepwiki_timeout",
            "DeepWiki MCP request timed out.",
            "Retry with a larger timeout or a shorter question.",
            context={"endpoint": endpoint, "repository": repo_name},
            retryable=True,
        ) from exc
    except Exception as exc:
        raise PrototypeError(
            "deepwiki_network_error",
            f"DeepWiki MCP request failed: {type(exc).__name__}.",
            "Check the hosted DeepWiki MCP service and retry.",
            context={"endpoint": endpoint, "repository": repo_name},
            retryable=True,
        ) from exc
    if _value(result, "isError", False):
        raise PrototypeError(
            "deepwiki_provider_error",
            _mcp_text(result) or "DeepWiki MCP returned an error.",
            "Check that the repository is public and retry the question.",
            context={"repository": repo_name},
        )
    prose = _mcp_text(result)
    if not prose:
        raise PrototypeError(
            "deepwiki_empty_response",
            "DeepWiki MCP returned no prose.",
            "Retry with a more specific repository question.",
            context={"repository": repo_name},
        )
    return prose


def _deduplicate(items: list[DocumentationInsight]) -> list[DocumentationInsight]:
    """Remove exact normalized duplicates while preserving order."""
    seen: set[str] = set()
    unique: list[DocumentationInsight] = []
    for item in items:
        key = re.sub(r"\s+", " ", f"{item.title}\n{item.content}").casefold().strip()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _format_insight(item: DocumentationInsight) -> str:
    """Render one attributed documentation insight."""
    source = f"\nSource: {item.source}" if item.source else ""
    if item.kind == "code":
        return f"**{item.title}**{source}\n```{item.language or ''}\n{item.content}\n```"
    return f"**{item.title}**{source}\n{item.content}"


def _merge_documentation(
    request: DocumentationRequest,
    repo: Repository,
    deepwiki_prose: str | None,
    context7_intelligence: list[DocumentationInsight],
) -> tuple[DocumentationResult, bool]:
    """Compose a readable answer from DeepWiki and Context7 evidence."""
    sections: list[str] = []
    if deepwiki_prose:
        sections.append(f"## Repository answer\n{deepwiki_prose}")
    references = [item for item in context7_intelligence if item.kind == "reference"]
    examples = [item for item in context7_intelligence if item.kind == "code"]
    if references:
        sections.append(
            "## Context7 documentation\n"
            + "\n\n".join(_format_insight(item) for item in references)
        )
    if examples:
        sections.append(
            "## Context7 examples\n" + "\n\n".join(_format_insight(item) for item in examples)
        )
    answer, truncated = _truncate("\n\n".join(sections), request.max_chars_total)
    if not answer:
        raise PrototypeError(
            "documentation_empty",
            "The providers returned no usable documentation.",
            "Retry with a more specific question.",
            context={"repository": repo.url, "question": request.question},
        )
    return (
        DocumentationResult(
            repository=repo.url,
            question=request.question,
            answer=answer,
            deepwiki_prose=deepwiki_prose,
            context7_intelligence=context7_intelligence,
        ),
        truncated,
    )


async def run_documentation(request: DocumentationRequest) -> SearchResponse:
    """Query Context7 and DeepWiki concurrently, then merge their results."""
    repo = _repository(request.repo_url)
    attempted = ["context7", "deepwiki"]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        outcomes = await asyncio.gather(
            _context7(request, repo, client),
            _deepwiki(request, repo),
            return_exceptions=True,
        )
    context7 = outcomes[0] if isinstance(outcomes[0], list) else None
    deepwiki = outcomes[1] if isinstance(outcomes[1], str) else None
    warnings = [
        _warning(provider, outcome)
        for provider, outcome in zip(attempted, outcomes, strict=True)
        if isinstance(outcome, BaseException)
    ]
    if context7 is None and deepwiki is None:
        raise PrototypeError(
            "documentation_providers_failed",
            "Context7 and DeepWiki both failed.",
            "Inspect the provider warnings and retry.",
            context={"warnings": [warning.model_dump(mode="json") for warning in warnings]},
            retryable=any(warning.retryable for warning in warnings),
        )
    result, truncated = _merge_documentation(request, repo, deepwiki, context7 or [])
    usage: list[ProviderUsage] = []
    if context7 is not None:
        usage.append(
            ProviderUsage(
                provider="context7",
                requests=2,
                endpoint="https://context7.com/api/v2",
                details={"library_id": request.context7_library_id or f"/{repo.owner}/{repo.name}"},
            )
        )
    if deepwiki is not None:
        usage.append(
            ProviderUsage(
                provider="deepwiki",
                requests=1,
                endpoint="https://mcp.deepwiki.com/mcp",
                details={"tool": "ask_question", "repository": f"{repo.owner}/{repo.name}"},
            )
        )
    return _response(
        "documentation",
        attempted_providers=attempted,
        providers=[
            provider
            for provider, value in (("context7", context7), ("deepwiki", deepwiki))
            if value is not None
        ],
        results=[result],
        warnings=warnings,
        usage=usage,
        truncated=truncated,
    )


def _provider_warning(provider: str, value: Any) -> ProviderWarning:
    """Normalize an in-band SDK warning into the common warning model."""
    kind = _text(_value(value, "code") or _value(value, "type")) or "provider_warning"
    message = _text(_value(value, "message") or _value(value, "error"))
    if not message and not isinstance(value, dict):
        message = _text(str(value))
    context = _value(value, "context")
    return ProviderWarning(
        provider=provider,
        kind=kind,
        message=message or "Provider returned a warning.",
        hint=_text(_value(value, "hint") or _value(value, "action"))
        or "Inspect the warning and retry if needed.",
        retryable=bool(_value(value, "retryable", False)),
        context=context if isinstance(context, dict) else {},
    )


async def run_versatile(request: VersatileRequest) -> SearchResponse:
    """Call Parallel advanced search and normalize its result objects."""
    api_key = os.environ.get("PARALLEL_API_KEY", "").strip()
    if not api_key:
        raise PrototypeError(
            "missing_credentials",
            "PARALLEL_API_KEY is not configured.",
            "Set PARALLEL_API_KEY before calling run_versatile.",
            context={"provider": "parallel"},
        )
    try:
        from parallel import AsyncParallel
    except ImportError as exc:
        raise PrototypeError(
            "missing_dependency",
            "The Parallel SDK is unavailable.",
            "Install the project's parallel-web dependency.",
            context={"package": "parallel-web"},
        ) from exc
    kwargs = {
        "search_queries": request.queries,
        "objective": request.objective,
        "mode": "advanced",
        "max_chars_total": request.max_chars_total,
        "advanced_settings": {
            "max_results": request.max_results,
            "fetch_policy": {"timeout_seconds": request.timeout_seconds},
        },
    }
    async with AsyncParallel(api_key=api_key) as client:
        raw_result = await client.search(**kwargs)
    results: list[WebResult] = []
    for item in _value(raw_result, "results", []) or []:
        excerpts = _value(item, "excerpts", []) or []
        if isinstance(excerpts, str):
            excerpts = [excerpts]
        results.append(
            WebResult(
                title=_field_text_or_none(item, "title"),
                url=_field_text_or_none(item, "url"),
                snippet="\n".join(part for part in map(_text, excerpts) if part),
                publish_date=_field_text_or_none(item, "publish_date"),
            )
        )
    warnings = [
        _provider_warning("parallel", item) for item in (_value(raw_result, "warnings", []) or [])
    ]
    bounded, truncated, omitted = _bound_search_items(results, request.max_chars_total)
    usage = ProviderUsage(
        provider="parallel",
        requests=1,
        endpoint="Parallel SDK advanced search",
        details={"search_id": _field_text_or_none(raw_result, "search_id")},
    )
    return _response(
        "versatile",
        attempted_providers=["parallel"],
        providers=["parallel"] if results else [],
        results=bounded,
        warnings=warnings,
        usage=[usage],
        truncated=truncated,
        omitted_results=omitted,
    )


def _is_youtube_url(value: str) -> bool:
    """Return whether a URL belongs to YouTube."""
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"} and (parsed.hostname or "").casefold() in _YOUTUBE_HOSTS
    )


def _renderer_text(value: Any) -> str:
    """Read text from a YouTube renderer field."""
    if not isinstance(value, dict):
        return ""
    simple = _text(value.get("simpleText"))
    if simple:
        return simple
    runs = value.get("runs")
    return (
        "".join(_text(run.get("text")) for run in runs if isinstance(run, dict))
        if isinstance(runs, list)
        else ""
    )


def _renderer_clean(renderer: dict[str, Any], key: str) -> str:
    """Return decoded, stripped text from one YouTube renderer field."""
    return html.unescape(_renderer_text(renderer.get(key))).strip()


def _iter_youtube_renderers(value: Any) -> Iterator[dict[str, Any]]:
    """Yield video renderers in document order."""
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            renderer = node.get("videoRenderer")
            if isinstance(renderer, dict):
                yield renderer
            children = [child for key, child in node.items() if key != "videoRenderer"]
            stack.extend(reversed(children))
        elif isinstance(node, list):
            stack.extend(reversed(node))


def _extract_youtube_data(page: str) -> dict[str, Any]:
    """Decode the embedded ``ytInitialData`` object."""
    match = re.search(r"ytInitialData\s*=\s*", page)
    start = page.find("{", match.end()) if match else -1
    if start < 0:
        raise PrototypeError(
            "youtube_html_invalid_response",
            "YouTube HTML did not contain ytInitialData.",
            "Retry the HTML fallback or configure another provider.",
        )
    try:
        data, _ = json.JSONDecoder().raw_decode(page, start)
    except json.JSONDecodeError as exc:
        raise PrototypeError(
            "youtube_html_invalid_response",
            "YouTube initial-data JSON could not be decoded.",
            "Retry the HTML fallback or configure another provider.",
        ) from exc
    if not isinstance(data, dict):
        raise PrototypeError(
            "youtube_html_invalid_response",
            "YouTube initial-data JSON was not an object.",
            "Retry the HTML fallback or configure another provider.",
        )
    return data


async def _youtube_api(
    request: YouTubeRequest, client: httpx.AsyncClient
) -> tuple[list[YouTubeVideo], ProviderUsage]:
    """Use the YouTube Data API as the strongest metadata source."""
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise PrototypeError(
            "missing_credentials",
            "GOOGLE_API_KEY is not configured.",
            "Set GOOGLE_API_KEY or allow the SearXNG/HTML fallbacks.",
            context={"provider": "youtube_api"},
        )
    params: dict[str, Any] = {
        "key": api_key,
        "q": request.query,
        "type": "video",
        "part": "snippet",
        "maxResults": min(request.max_results, 50),
        "safeSearch": "moderate",
    }
    language = os.environ.get("YOUTUBE_API_LANGUAGE", "").strip()
    if language:
        params["relevanceLanguage"] = language
    region = os.environ.get("YOUTUBE_API_REGION", "").strip()
    if region:
        params["regionCode"] = region
    response = await client.get(
        "https://www.googleapis.com/youtube/v3/search",
        params=params,
        headers={"Accept": "application/json"},
        timeout=request.timeout_seconds,
    )
    try:
        data = response.json()
    except ValueError as exc:
        raise PrototypeError(
            "youtube_api_invalid_response",
            "YouTube Data API returned invalid JSON.",
            "Retry the API request or use a fallback provider.",
        ) from exc
    if response.status_code >= 400:
        reason = ""
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                errors = error.get("errors")
                if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                    reason = _text(errors[0].get("reason"))
        raise PrototypeError(
            "youtube_api_error",
            f"YouTube Data API returned HTTP {response.status_code}{f' ({reason})' if reason else ''}.",
            "Check GOOGLE_API_KEY, API enablement, and quota; a fallback may still work.",
            context={"status": response.status_code, "reason": reason or None},
            retryable=response.status_code in {429, 500, 502, 503, 504},
        )
    videos: list[YouTubeVideo] = []
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        video_id = raw_id.get("videoId") if isinstance(raw_id, dict) else None
        raw_snippet = item.get("snippet")
        snippet = raw_snippet if isinstance(raw_snippet, dict) else {}
        video_id = _text(video_id)
        title = _text(snippet.get("title"))
        if not video_id or not title:
            continue
        videos.append(
            YouTubeVideo(
                provider="youtube_api",
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                snippet=_text(snippet.get("description")),
                video_id=video_id,
                channel_id=_text_or_none(snippet.get("channelId")),
                channel_title=_text_or_none(snippet.get("channelTitle")),
                published_at=_text_or_none(snippet.get("publishedAt")),
            )
        )
    return videos, ProviderUsage(
        provider="youtube_api",
        requests=1,
        endpoint="https://www.googleapis.com/youtube/v3/search",
        details={"method": "search.list", "quota_units": 1},
    )


async def _youtube_searxng(
    request: YouTubeRequest, client: httpx.AsyncClient
) -> tuple[list[YouTubeVideo], ProviderUsage]:
    """Use configured SearXNG as the middle fallback."""
    base_url = os.environ.get("SEARXNG_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise PrototypeError(
            "missing_configuration",
            "SEARXNG_BASE_URL is not configured.",
            "Set SEARXNG_BASE_URL or configure GOOGLE_API_KEY.",
            context={"provider": "searxng_youtube"},
        )
    response = await client.get(
        f"{base_url}/search",
        params={
            "q": request.query,
            "format": "json",
            "engines": os.environ.get("YOUTUBE_SEARCH_ENGINE", "youtube"),
        },
        headers={"Accept": "application/json", "User-Agent": "quick-web-search-v2-prototype"},
        timeout=request.timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise PrototypeError(
            "searxng_invalid_response",
            "SearXNG returned no result list for YouTube.",
            "Check the endpoint and enable its YouTube engine.",
        )
    videos: list[YouTubeVideo] = []
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        url, title = _text(item.get("url")), _text(item.get("title"))
        if not title or not _is_youtube_url(url):
            continue
        videos.append(
            YouTubeVideo(
                provider="searxng_youtube",
                title=title,
                url=url,
                snippet=_text(item.get("content")),
                channel_title=_text_or_none(item.get("author")),
                published_at=_text_or_none(item.get("publishedDate") or item.get("published_date")),
                duration=_text_or_none(item.get("length")),
                views=_text_or_none(item.get("views")),
            )
        )
        if len(videos) >= request.max_results:
            break
    return videos, ProviderUsage(
        provider="searxng_youtube",
        requests=1,
        endpoint=f"{base_url}/search",
        details={"engine": os.environ.get("YOUTUBE_SEARCH_ENGINE", "youtube")},
    )


async def _youtube_html(
    request: YouTubeRequest, client: httpx.AsyncClient
) -> tuple[list[YouTubeVideo], ProviderUsage]:
    """Use YouTube search HTML as the final fallback."""
    response = await client.get(
        "https://www.youtube.com/results",
        params={"search_query": request.query},
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
            "User-Agent": "quick-web-search-v2-prototype/1",
        },
        timeout=request.timeout_seconds,
    )
    response.raise_for_status()
    videos: list[YouTubeVideo] = []
    for renderer in _iter_youtube_renderers(_extract_youtube_data(response.text)):
        video_id = _text(renderer.get("videoId"))
        title = _renderer_clean(renderer, "title")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or not title:
            continue
        channel = _renderer_clean(renderer, "ownerText")
        duration = _renderer_clean(renderer, "lengthText")
        views = _renderer_clean(renderer, "viewCountText")
        published = _renderer_clean(renderer, "publishedTimeText")
        videos.append(
            YouTubeVideo(
                provider="youtube_html",
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                snippet=" | ".join(part for part in (duration, channel, views, published) if part),
                video_id=video_id,
                channel_title=channel or None,
                published_at=published or None,
                duration=duration or None,
                views=views or None,
            )
        )
        if len(videos) >= request.max_results:
            break
    return videos, ProviderUsage(
        provider="youtube_html",
        requests=1,
        endpoint="https://www.youtube.com/results",
        details={"quota_units": 0},
    )


async def run_youtube(request: YouTubeRequest) -> SearchResponse:
    """Run YouTube API → SearXNG → HTML until a provider returns videos."""
    warnings: list[ProviderWarning] = []
    usage: list[ProviderUsage] = []
    videos: list[YouTubeVideo] = []
    providers: tuple[tuple[str, YouTubeProviderFn], ...] = (
        ("youtube_api", _youtube_api),
        ("searxng_youtube", _youtube_searxng),
        ("youtube_html", _youtube_html),
    )
    attempted: list[str] = []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for name, provider in providers:
            attempted.append(name)
            videos = await _attempt_youtube_provider(
                name,
                provider,
                request,
                client,
                warnings,
                usage,
            )
            if videos:
                break

    if not videos:
        raise PrototypeError(
            "youtube_search_failed",
            "All YouTube providers returned no usable videos.",
            "Configure GOOGLE_API_KEY or SEARXNG_BASE_URL and retry.",
            context={
                "query": request.query,
                "warnings": [warning.model_dump(mode="json") for warning in warnings],
            },
            retryable=any(warning.retryable for warning in warnings),
        )
    bounded, truncated, omitted = _bound_search_items(videos, request.max_chars_total)
    return _response(
        "youtube",
        attempted_providers=attempted,
        providers=list(dict.fromkeys(video.provider for video in videos)),
        results=bounded,
        warnings=warnings,
        usage=usage,
        truncated=truncated,
        omitted_results=omitted,
    )


async def run_v2(
    request: VersatileRequest | DocumentationRequest | YouTubeRequest,
) -> SearchResponse:
    """Dispatch a validated request to its mode runner."""
    if isinstance(request, VersatileRequest):
        return await run_versatile(request)
    if isinstance(request, DocumentationRequest):
        return await run_documentation(request)
    if isinstance(request, YouTubeRequest):
        return await run_youtube(request)
    raise TypeError(f"Unsupported request model: {type(request).__name__}")
