"""Library documentation discovery for quick web search (Context7 + DeepWiki).

Ported from ``prototypes/quick_web_search_v2.py`` and wired to
:mod:`~kindly_web_search_mcp_server.settings` instead of raw environment
reads. This module replaces ``tools/code_search/docs.py``; the remaining
code-search modes (code, discovery, issues, huggingface) are untouched.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field

from ...settings import settings

LOGGER = logging.getLogger(__name__)

_DEEPWIKI_URL = "https://mcp.deepwiki.com/mcp"
_CONTEXT7_URL = "https://context7.com/api"

_DEFAULT_TIMEOUT_SECONDS = 45.0
_DEFAULT_MAX_CHARS = 12_000


class DocumentationInsight(BaseModel):
    """Context7 reference or code example retained with provenance."""

    kind: str = "reference"
    title: str
    content: str
    language: str | None = None
    source: str | None = None


class DocsWarning(BaseModel):
    """A provider failure retained beside a partial successful response."""

    provider: str
    kind: str
    message: str
    hint: str
    retryable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class DocsUsage(BaseModel):
    """Small common record of external requests made by the mode."""

    provider: str
    requests: int
    endpoint: str
    details: dict[str, Any] = Field(default_factory=dict)


class DocsOutcome(BaseModel):
    """Merged documentation answer with provider evidence."""

    repository: str
    question: str
    answer: str
    deepwiki_prose: str | None = None
    insights: list[DocumentationInsight] = Field(default_factory=list)
    truncated: bool = False
    warnings: list[DocsWarning] = Field(default_factory=list)
    usage: list[DocsUsage] = Field(default_factory=list)


def _text(value: Any) -> str:
    """Return stripped string text, or an empty string for other values."""
    return value.strip() if isinstance(value, str) else ""


def _parse_repository(repo_url: str) -> tuple[str, str, str]:
    """Validate a public GitHub URL and return ``(url, owner, name)``.

    Raises:
        ValueError: With a correction example when the URL is not usable.
    """
    value = _text(repo_url)
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").casefold() not in {"github.com", "www.github.com"}
        or len(parts) < 2
    ):
        raise ValueError(
            "mode='docs' requires a public GitHub repository URL, "
            "e.g. repo_url='https://github.com/owner/repository'."
        )
    owner, name, *_ = parts
    return value.rstrip("/"), owner, name.removesuffix(".git")


def _warn(provider: str, error: BaseException) -> DocsWarning:
    """Convert a provider exception into warning data."""
    return DocsWarning(
        provider=provider,
        kind=type(error).__name__,
        message=_text(str(error))[:300] or type(error).__name__,
        hint="Inspect provider connectivity and retry.",
        retryable=True,
    )


async def _context7_get(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    """Call a Context7 endpoint and validate its JSON object response."""
    headers = {"Accept": "application/json"}
    token = settings.context7_api_key.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = await client.get(
            f"{_CONTEXT7_URL}{path}",
            params=params,
            headers=headers,
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Context7 request failed: {type(exc).__name__}.") from exc
    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            detail = (
                _text(body.get("message") or body.get("error")) if isinstance(body, dict) else ""
            )
        except ValueError:
            pass
        raise RuntimeError(
            f"Context7 returned HTTP {response.status_code}"
            f"{f': {detail}' if detail else ''}. Check the library ID and query."
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Context7 returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Context7 returned a non-object JSON response.")
    return data


async def _context7(
    question: str,
    library_hint: str,
    timeout_seconds: float,
    client: httpx.AsyncClient,
) -> list[DocumentationInsight]:
    """Resolve a library and retrieve its ranked Context7 documentation."""
    search_data = await _context7_get(
        client,
        "/v2/libs/search",
        params={"libraryName": library_hint.lstrip("/"), "query": question},
        timeout_seconds=timeout_seconds,
    )
    selected_id = library_hint
    for item in search_data.get("results", []) or []:
        if not isinstance(item, dict):
            continue
        item_id = _text(item.get("id"))
        if item_id:
            selected_id = item_id
            break
    data = await _context7_get(
        client,
        "/v2/context",
        params={"libraryId": selected_id, "query": question, "type": "json"},
        timeout_seconds=timeout_seconds,
    )
    insights: list[DocumentationInsight] = []
    for item in data.get("infoSnippets", []) or []:
        if not isinstance(item, dict):
            continue
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
    for group in data.get("codeSnippets", []) or []:
        if not isinstance(group, dict):
            continue
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
    content = (
        result.get("content", []) if isinstance(result, dict) else getattr(result, "content", [])
    )
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        prose = "\n".join(
            text
            for item in content
            if (
                text := _text(
                    item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
                )
            )
        )
        if prose:
            return prose
    structured = (
        result.get("structuredContent")
        if isinstance(result, dict)
        else getattr(result, "structuredContent", None)
    )
    if isinstance(structured, dict):
        for key in ("answer", "content", "text", "response"):
            if prose := _text(structured.get(key)):
                return prose
    return ""


async def _deepwiki(question: str, repo_name: str, timeout_seconds: float) -> str:
    """Ask DeepWiki's ``ask_question`` tool over Streamable HTTP."""
    try:
        async with asyncio.timeout(timeout_seconds):
            async with streamable_http_client(_DEEPWIKI_URL) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "ask_question",
                        arguments={"repoName": repo_name, "question": question},
                    )
    except TimeoutError as exc:
        raise RuntimeError("DeepWiki MCP request timed out; retry with a larger timeout.") from exc
    except Exception as exc:
        raise RuntimeError(f"DeepWiki MCP request failed: {type(exc).__name__}.") from exc
    is_error = (
        result.get("isError", False)
        if isinstance(result, dict)
        else getattr(result, "isError", False)
    )
    if is_error:
        raise RuntimeError(_mcp_text(result) or "DeepWiki MCP returned an error.")
    prose = _mcp_text(result)
    if not prose:
        raise RuntimeError("DeepWiki MCP returned no prose; retry with a more specific question.")
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


async def fetch_docs_outcome(
    repo_url: str,
    question: str,
    *,
    context7_library_id: str | None = None,
    max_results: int = 5,
    max_chars_total: int = _DEFAULT_MAX_CHARS,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> DocsOutcome:
    """Query Context7 and DeepWiki concurrently, then merge their results.

    Args:
        repo_url: Public GitHub repository URL.
        question: Documentation question driving retrieval.
        context7_library_id: Optional explicit Context7 library ID override.
        max_results: Upper bound on retained Context7 insights.
        max_chars_total: Upper bound on merged answer characters.
        timeout_seconds: Per-provider timeout.

    Returns:
        DocsOutcome with the merged answer and provider evidence.

    Raises:
        ValueError: If inputs are blank or the repository URL is unusable.
        RuntimeError: If both providers fail or yield nothing usable.
    """
    if not question or not question.strip():
        raise ValueError("mode='docs' requires a non-blank question.")
    url, owner, name = _parse_repository(repo_url)
    repo_name = f"{owner}/{name}"
    library_hint = (context7_library_id or "").strip() or f"/{repo_name}"
    warnings: list[DocsWarning] = []
    usage: list[DocsUsage] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        outcomes = await asyncio.gather(
            _context7(question.strip(), library_hint, timeout_seconds, client),
            _deepwiki(question.strip(), repo_name, timeout_seconds),
            return_exceptions=True,
        )
    context7 = outcomes[0] if isinstance(outcomes[0], list) else None
    deepwiki = outcomes[1] if isinstance(outcomes[1], str) else None
    for provider, outcome in (("context7", outcomes[0]), ("deepwiki", outcomes[1])):
        if isinstance(outcome, BaseException):
            warnings.append(_warn(provider, outcome))
    if context7 is None and deepwiki is None:
        raise RuntimeError("Context7 and DeepWiki both failed; inspect warnings and retry.")
    insights = (context7 or [])[: max(1, max_results)]
    sections: list[str] = []
    if deepwiki:
        sections.append(f"## Repository answer\n{deepwiki}")
    references = [item for item in insights if item.kind == "reference"]
    examples = [item for item in insights if item.kind == "code"]
    if references:
        sections.append(
            "## Context7 documentation\n" + "\n\n".join(map(_format_insight, references))
        )
    if examples:
        sections.append("## Context7 examples\n" + "\n\n".join(map(_format_insight, examples)))
    answer = "\n\n".join(sections)
    truncated = False
    if len(answer) > max_chars_total:
        answer = f"{answer[: max(0, max_chars_total - 1)]}…"
        truncated = True
    if not answer:
        raise RuntimeError("The providers returned no usable documentation.")
    if context7 is not None:
        usage.append(
            DocsUsage(
                provider="context7",
                requests=2,
                endpoint="https://context7.com/api/v2",
                details={"library_id": (context7_library_id or "").strip() or f"/{repo_name}"},
            )
        )
    if deepwiki is not None:
        usage.append(
            DocsUsage(
                provider="deepwiki",
                requests=1,
                endpoint=_DEEPWIKI_URL,
                details={"tool": "ask_question", "repository": repo_name},
            )
        )
    return DocsOutcome(
        repository=url,
        question=question.strip(),
        answer=answer,
        deepwiki_prose=deepwiki,
        insights=insights,
        truncated=truncated,
        warnings=warnings,
        usage=usage,
    )
