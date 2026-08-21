"""GitHub code-search adapter with typed partial-failure diagnostics."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from collections import defaultdict, deque
from typing import Any, Iterable

import httpx

from ...settings import settings
from .models import (
    CodeSearchHit,
    CodeSearchRequest,
    Diagnostic,
    ProviderResponse,
    RepoCandidate,
    TextFragment,
    build_location_metadata,
)
from .query import QueryPlan
from .tree_sitter_evidence import classify_source, language_for_path

LOGGER = logging.getLogger(__name__)

_GITHUB_API_URL = "https://api.github.com"
_GITHUB_GRAPHQL_URL = f"{_GITHUB_API_URL}/graphql"
_GITHUB_CODE_SEARCH_URL = f"{_GITHUB_API_URL}/search/code"
_GITHUB_ACCEPT = "application/vnd.github+json"
_GITHUB_TEXT_MATCH_ACCEPT = "application/vnd.github.text-match+json"
_GITHUB_API_VERSION = "2022-11-28"
_CODE_SEARCH_QUALIFIERS = {
    "extension",
    "file",
    "filename",
    "fork",
    "in",
    "lang",
    "language",
    "org",
    "path",
    "repo",
    "size",
    "user",
    "-extension",
    "-file",
    "-filename",
    "-lang",
    "-language",
    "-org",
    "-path",
    "-repo",
    "-user",
}
_REPOSITORY_SEARCH_QUALIFIERS = {
    "archived",
    "created",
    "followers",
    "fork",
    "forks",
    "good-first-issues",
    "help-wanted-issues",
    "in",
    "language",
    "license",
    "mirror",
    "org",
    "pushed",
    "size",
    "sponsorships",
    "stars",
    "template",
    "topic",
    "topics",
    "user",
    "visibility",
}
_REPOSITORY_GENERIC_TERMS = {
    "code",
    "discover",
    "example",
    "examples",
    "existing",
    "find",
    "implement",
    "implementation",
    "implementations",
    "implementing",
    "mass",
    "open",
    "project",
    "projects",
    "repo",
    "repos",
    "repository",
    "repositories",
    "search",
    "source",
}
_LANGUAGE_NAMES = {
    "c": "C",
    "c#": "C#",
    "c++": "C++",
    "dart": "Dart",
    "go": "Go",
    "java": "Java",
    "javascript": "JavaScript",
    "kotlin": "Kotlin",
    "lua": "Lua",
    "objective-c": "Objective-C",
    "php": "PHP",
    "powershell": "PowerShell",
    "python": "Python",
    "r": "R",
    "ruby": "Ruby",
    "rust": "Rust",
    "scala": "Scala",
    "shell": "Shell",
    "swift": "Swift",
    "typescript": "TypeScript",
}
_REPOSITORY_NOISE_TERMS = {
    "awesome",
    "bookmarks",
    "curated list",
    "resources",
    "tips",
    "tricks",
    "wordlist",
}
_LOW_VALUE_GLOBAL_DISCOVERY_PATH = re.compile(
    r"(?:^|/)(?:readme(?:\.[^/]*)?|changelog(?:\.[^/]*)?|changes(?:\.[^/]*)?|"
    r"news(?:\.[^/]*)?|docs?/.*|examples?/.*)|"
    r"\.(?:csv|jsonl|list|md|markdown|rst|txt)$",
    re.IGNORECASE,
)

_DISCOVER_QUERY = """
query DiscoverRepositories($q: String!, $first: Int!) {
  search(type: REPOSITORY, query: $q, first: $first) {
    repositoryCount
    nodes {
      ... on Repository {
        nameWithOwner
        url
        description
        stargazerCount
        forkCount
        pushedAt
        homepageUrl
        isArchived
        isFork
        primaryLanguage { name }
        licenseInfo { spdxId }
        repositoryTopics(first: 10) {
          nodes { topic { name } }
        }
        defaultBranchRef {
          name
          target { oid }
        }
      }
    }
  }
}
"""


def _token() -> str | None:
    value = os.environ.get("GITHUB_TOKEN", "").strip()
    if value:
        return value
    value = os.environ.get("GH_TOKEN", "").strip()
    return value or None


def _headers(token: str, *, text_matches: bool = False) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": _GITHUB_TEXT_MATCH_ACCEPT if text_matches else _GITHUB_ACCEPT,
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "User-Agent": "web-search-mcp/code-search",
    }


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _diagnostic(
    message: str,
    *,
    outcome: str = "error",
    failure_kind: str = "provider",
    query: str | None = None,
    response: httpx.Response | None = None,
    details: dict[str, Any] | None = None,
) -> Diagnostic:
    status = response.status_code if response is not None else None
    kind = failure_kind
    if status in {401, 403} and failure_kind == "provider":
        kind = "auth" if status == 401 else "rate_limit"
    if status == 404 and failure_kind == "provider":
        kind = "not_found"
    if status in {408, 429, 500, 502, 503, 504} and failure_kind == "provider":
        kind = "rate_limit" if status == 429 else "network"
    return Diagnostic(
        provider="github",
        outcome=outcome,  # type: ignore[arg-type]
        message=message[:500],
        failure_kind=kind,  # type: ignore[arg-type]
        status_code=status,
        retry_after_seconds=_retry_after(response) if response is not None else None,
        query=query,
        details=details or {},
    )


def _json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _graphql_error_message(payload: dict[str, Any]) -> str:
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return "GitHub GraphQL returned an invalid payload"
    messages = [
        item.get("message", "").strip()
        for item in errors
        if isinstance(item, dict) and isinstance(item.get("message"), str)
    ]
    return "; ".join(item for item in messages if item)[:500] or "GitHub GraphQL error"


def _repo_candidate(node: dict[str, Any], rank: int | None = None) -> RepoCandidate | None:
    name = node.get("nameWithOwner")
    if not isinstance(name, str) or "/" not in name:
        return None
    language = node.get("primaryLanguage")
    target = node.get("defaultBranchRef")
    target_oid = target.get("target", {}).get("oid") if isinstance(target, dict) else None
    topics_connection = node.get("repositoryTopics")
    topic_nodes = topics_connection.get("nodes", []) if isinstance(topics_connection, dict) else []
    topics = [
        topic_name
        for item in topic_nodes
        if isinstance(item, dict)
        and isinstance(item.get("topic"), dict)
        and isinstance((topic_name := item["topic"].get("name")), str)
    ]
    license_info = node.get("licenseInfo")
    return RepoCandidate(
        name_with_owner=name,
        url=node.get("url") if isinstance(node.get("url"), str) else f"https://github.com/{name}",
        description=(node.get("description") or "")[:500] or None,
        stars=int(node.get("stargazerCount") or 0),
        forks=int(node.get("forkCount") or 0),
        pushed_at=node.get("pushedAt") if isinstance(node.get("pushedAt"), str) else None,
        language=(language.get("name") if isinstance(language, dict) else None),
        topics=topics,
        license_spdx_id=(license_info.get("spdxId") if isinstance(license_info, dict) else None),
        homepage_url=node.get("homepageUrl") if isinstance(node.get("homepageUrl"), str) else None,
        default_branch=(target.get("name") if isinstance(target, dict) else None),
        head_oid=target_oid if isinstance(target_oid, str) else None,
        archived=bool(node.get("isArchived")),
        fork=bool(node.get("isFork")),
        discovery_rank=rank,
    )


def _scope_parts(value: str) -> tuple[str, str] | None:
    cleaned = value.strip().removeprefix("https://github.com/").strip("/")
    parts = cleaned.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def _query_qualifier(plan: QueryPlan, key: str) -> list[str]:
    return [value for item_key, value in plan.qualifiers if item_key == key]


def _scope_repositories(
    request: CodeSearchRequest, plan: QueryPlan
) -> tuple[list[str], list[Diagnostic]]:
    candidates = list(request.repositories) or _query_qualifier(plan, "repo")
    scopes: list[str] = []
    diagnostics: list[Diagnostic] = []
    for value in candidates:
        if not _scope_parts(value):
            diagnostics.append(
                _diagnostic(
                    f"Repository scope requires owner/repo: {value!r}",
                    failure_kind="validation",
                    query=request.query,
                )
            )
            continue
        if value not in scopes:
            scopes.append(value)
    return scopes, diagnostics


async def _discover_repositories(
    client: httpx.AsyncClient,
    plan: QueryPlan,
    request: CodeSearchRequest,
    token: str,
) -> tuple[list[RepoCandidate], list[Diagnostic], int]:
    queries = _build_repository_queries(plan)
    if not queries:
        return [], [], 0
    responses = await asyncio.gather(
        *(
            client.post(
                _GITHUB_GRAPHQL_URL,
                headers=_headers(token),
                json={
                    "query": _DISCOVER_QUERY,
                    "variables": {
                        "q": query[:_QUERY_MAX_CHARS],
                        "first": request.budget.max_repositories,
                    },
                },
                timeout=settings.search_retrieve_budget_seconds,
            )
            for query in queries
        ),
        return_exceptions=True,
    )
    diagnostics: list[Diagnostic] = []
    merged: dict[str, RepoCandidate] = {}
    for query, response in zip(queries, responses, strict=True):
        if isinstance(response, BaseException):
            diagnostics.append(
                _diagnostic(
                    f"GitHub repository discovery failed: {type(response).__name__}",
                    outcome="partial",
                    failure_kind="network",
                    query=query,
                )
            )
            continue
        if response.status_code != 200:
            diagnostics.append(
                _diagnostic(
                    f"GitHub repository discovery returned HTTP {response.status_code}",
                    query=query,
                    response=response,
                )
            )
            continue
        payload = _json(response)
        if payload is None:
            diagnostics.append(
                _diagnostic("GitHub repository discovery returned invalid JSON", query=query)
            )
            continue
        if payload.get("errors"):
            diagnostics.append(
                _diagnostic(_graphql_error_message(payload), outcome="partial", query=query)
            )
        nodes = ((payload.get("data") or {}).get("search") or {}).get("nodes", [])
        if not isinstance(nodes, list):
            diagnostics.append(
                _diagnostic("GitHub repository discovery returned no node list", query=query),
            )
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            candidate = _repo_candidate(node)
            if candidate is None:
                continue
            existing = merged.get(candidate.name_with_owner)
            if existing is None:
                candidate.discovery_rank = len(merged) + 1
                candidate.discovery_queries = [query]
                merged[candidate.name_with_owner] = candidate
            elif query not in existing.discovery_queries:
                existing.discovery_queries.append(query)
    repos = _rank_repository_candidates(plan, merged.values())
    return repos[: request.budget.max_repositories], diagnostics, len(queries)


def _qualifier_text(
    plan: QueryPlan,
    allowed: set[str],
    *,
    exclude: set[str] | None = None,
    in_values: set[str] | None = None,
) -> str:
    excluded = exclude or set()
    parts: list[str] = []
    for key, value in plan.qualifiers:
        if key not in allowed or key in excluded:
            continue
        canonical_key = key
        if key == "file":
            canonical_key = "filename"
        elif key == "-file":
            canonical_key = "-filename"
        elif key == "lang":
            canonical_key = "language"
        elif key == "-lang":
            canonical_key = "-language"

        if key == "in" and in_values is not None:
            selected = [
                item.strip() for item in value.strip('"').split(",") if item.strip() in in_values
            ]
            if not selected:
                continue
            value = ",".join(selected)
        parts.append(f"{canonical_key}:{value}")
    return " ".join(parts)

def _repository_terms(plan: QueryPlan) -> tuple[list[str], str | None]:
    terms: list[str] = []
    inferred_language: str | None = None
    explicit_language = bool(_query_qualifier(plan, "language"))
    for term in plan.concept_terms or tuple(plan.search_text.split()):
        cleaned = term.strip("\"'(),")
        folded = cleaned.casefold()
        if not cleaned or folded in _REPOSITORY_GENERIC_TERMS:
            continue
        if not explicit_language and folded in _LANGUAGE_NAMES:
            inferred_language = inferred_language or _LANGUAGE_NAMES[folded]
            continue
        if folded not in {item.casefold() for item in terms}:
            terms.append(cleaned)
    return terms, inferred_language


def _build_repository_query(plan: QueryPlan) -> str:
    terms, inferred_language = _repository_terms(plan)
    qualifiers = _qualifier_text(
        plan,
        _REPOSITORY_SEARCH_QUALIFIERS,
        in_values={"description", "name", "readme"},
    )
    parts = [" ".join(terms), qualifiers]
    if inferred_language:
        parts.append(f"language:{inferred_language}")
    if not _query_qualifier(plan, "in"):
        parts.append("in:name,description,readme")
    return " ".join(part for part in parts if part)[:_QUERY_MAX_CHARS].rstrip()


def _build_repository_queries(plan: QueryPlan) -> tuple[str, ...]:
    relaxed = _build_repository_query(plan)
    original = plan.original_query.casefold()
    phrases: list[str] = []
    if "code search" in original:
        phrases.append('"code search"')
    if "github api" in original:
        phrases.append('"GitHub API"')
    precise = " ".join([*phrases, relaxed])[:_QUERY_MAX_CHARS].rstrip()
    return tuple(dict.fromkeys(query for query in (precise, relaxed) if query))


def _repository_proof_variants(plan: QueryPlan, variants: tuple[str, ...]) -> tuple[str, ...]:
    original = plan.original_query.casefold()
    if "code search" in original:
        return ("code_search", "search_code")
    if "github api" in original:
        return ("GitHub API",)
    return (variants[-1],)


def _is_low_value_global_discovery_hit(hit: CodeSearchHit) -> bool:
    """Reject broad discovery artifacts that cannot prove an implementation."""

    return bool(hit.path and _LOW_VALUE_GLOBAL_DISCOVERY_PATH.search(hit.path))


def _rank_repository_candidates(
    plan: QueryPlan, candidates: Iterable[RepoCandidate]
) -> list[RepoCandidate]:
    query_terms, _ = _repository_terms(plan)
    query_terms = [item.casefold() for item in query_terms]
    wants_code_search = "code search" in plan.original_query.casefold()
    ranked: list[RepoCandidate] = []
    for candidate in candidates:
        name = candidate.name_with_owner.casefold().replace("-", " ").replace("_", " ")
        description = (candidate.description or "").casefold()
        topics = " ".join(candidate.topics).casefold()
        score = 0.0
        for term in query_terms:
            if term in name:
                score += 1.2
            elif term in description:
                score += 0.55
            elif term in topics:
                score += 0.4
        if wants_code_search:
            if "code search" in name:
                score += 3.0
            if "code search" in description:
                score += 1.8
        score += 0.35 * min(2, len(candidate.discovery_queries))
        score += 0.05 * math.log1p(candidate.stars)
        noise_text = f"{name} {description}"
        score -= 1.25 * sum(term in noise_text for term in _REPOSITORY_NOISE_TERMS)
        candidate.discovery_score = round(score, 6)
        ranked.append(candidate)
    ranked.sort(
        key=lambda item: (
            -item.discovery_score,
            -len(item.discovery_queries),
            -item.stars,
            item.name_with_owner.casefold(),
        )
    )
    for rank, candidate in enumerate(ranked, 1):
        candidate.discovery_rank = rank
    return ranked


def _legacy_regex_anchor(variant: str, plan: QueryPlan) -> str:
    literals = re.sub(r"\\(.)", r"\1", variant)
    literals = re.sub(r"[\[\]().*+?{}^$|]", " ", literals)
    terms = [item.strip("\"'") for item in literals.split() if len(item.strip("\"'")) >= 3]
    if not terms:
        terms = list(plan.anchor_terms)
    return max(terms, key=len, default="")


def _build_code_query(
    variant: str,
    *,
    scope: str | None,
    request: CodeSearchRequest,
    plan: QueryPlan,
) -> str:
    search_terms = _legacy_regex_anchor(variant, plan) if plan.regex_source else variant.strip()
    parts = [search_terms]
    if scope and not re.search(r"(?:^|\s)repo:", variant, re.I):
        parts.append(f"repo:{scope}")
    parts.append(
        _qualifier_text(
            plan,
            _CODE_SEARCH_QUALIFIERS,
            exclude={"repo"},
            in_values={"file", "path"},
        )
    )
    return " ".join(part for part in parts if part)[:_QUERY_MAX_CHARS].rstrip()


def _fragment_from_match(match: dict[str, Any]) -> TextFragment | None:
    fragment = match.get("fragment")
    if not isinstance(fragment, str) or not fragment.strip():
        return None
    matches = match.get("matches")
    metadata = {
        key: match[key] for key in ("property", "object_url", "object_type") if key in match
    }
    if isinstance(matches, list):
        metadata["matches"] = matches
    return TextFragment(text=fragment[:10_000], match_metadata=metadata)


def _parse_code_items(
    payload: dict[str, Any],
    *,
    provider: str,
    query_variant: str,
    page: int,
    per_page: int,
    max_results: int,
) -> tuple[list[CodeSearchHit], int, bool]:
    items = payload.get("items")
    if not isinstance(items, list):
        return [], int(payload.get("total_count") or 0), bool(payload.get("incomplete_results"))
    hits: list[CodeSearchHit] = []
    for local_index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        repository_data = item.get("repository")
        repository = repository_data.get("full_name") if isinstance(repository_data, dict) else None
        path = item.get("path")
        url = item.get("html_url")
        if not isinstance(repository, str) or not repository.strip() or not isinstance(path, str):
            continue
        if not isinstance(url, str) or not url.strip():
            url = f"https://github.com/{repository}/blob/{item.get('sha', 'HEAD')}/{path}"
        revision_match = re.search(r"/blob/([^/]+)/", url)
        fragments: list[TextFragment] = []
        match_spans: list[dict[str, Any]] = []
        text_matches = item.get("text_matches")
        if isinstance(text_matches, list):
            for fragment_index, match in enumerate(text_matches[:10]):
                if isinstance(match, dict) and (fragment := _fragment_from_match(match)):
                    fragments.append(fragment)
                    for matched in match.get("matches", []):
                        if not isinstance(matched, dict):
                            continue
                        indices = matched.get("indices")
                        if (
                            isinstance(indices, list)
                            and len(indices) == 2
                            and all(isinstance(value, int) for value in indices)
                        ):
                            match_spans.append(
                                {
                                    "fragment": fragment_index,
                                    "start": indices[0],
                                    "end": indices[1],
                                    "text": matched.get("text"),
                                }
                            )
        hits.append(
            CodeSearchHit(
                repository=repository,
                path=path,
                sha=item.get("sha") if isinstance(item.get("sha"), str) else None,
                commit_oid=revision_match.group(1) if revision_match else None,
                url=url,
                provider=provider,
                query_variant=query_variant,
                search_rank=(page - 1) * per_page + local_index + 1,
                result_kind="code_match",
                location=build_location_metadata(
                    repository=repository,
                    path=path,
                    url=url,
                    revision=revision_match.group(1) if revision_match else None,
                    match_data_available=True,
                ),
                fragments=fragments,
                match_spans=match_spans,
                snippet="\n".join(fragment.text for fragment in fragments)[:200_000] or None,
                score_components={"provider_score": float(item.get("score") or 0.0)},
                source_metadata={
                    "repository_url": repository_data.get("html_url")
                    if isinstance(repository_data, dict)
                    else None,
                    "api_url": item.get("url"),
                    "git_url": item.get("git_url"),
                    "archived": bool(repository_data.get("archived"))
                    if isinstance(repository_data, dict)
                    else False,
                    "stars": int(repository_data.get("stargazers_count") or 0)
                    if isinstance(repository_data, dict)
                    else 0,
                    "forks": int(repository_data.get("forks_count") or 0)
                    if isinstance(repository_data, dict)
                    else 0,
                    "omitted_fragments": max(0, len(text_matches) - 10)
                    if isinstance(text_matches, list)
                    else 0,
                },
            )
        )
        if len(hits) >= max_results:
            break
    return hits, int(payload.get("total_count") or 0), bool(payload.get("incomplete_results"))


class _RequestGate:
    """Request-budget counter plus a sliding-window per-minute rate limiter.

    ``maximum`` preserves the existing budget semantics (reserve() returns
    False once ``count`` reaches it). ``max_per_minute`` enforces GitHub's
    hard /search/code rate limit via a sliding 60-second window of
    reservation timestamps.

    The default ``max_per_minute=10`` matches GitHub's hard limit exactly so
    a plain gate can still drive the 10-page deep-pagination loop;
    ``search_github`` explicitly passes ``max_per_minute=8`` to keep headroom.
    """

    def __init__(self, maximum: int, max_per_minute: int = 10) -> None:
        self.maximum = maximum
        self.count = 0
        self.max_per_minute = max_per_minute
        self._lock = asyncio.Lock()
        self._window: deque[float] = deque()

    @property
    def rate_limited(self) -> bool:
        """True when the per-minute sliding window is currently full."""
        now = time.monotonic()
        while self._window and now - self._window[0] >= 60.0:
            self._window.popleft()
        return len(self._window) >= self.max_per_minute

    @property
    def budget_exhausted(self) -> bool:
        """True when the total request budget (``maximum``) is consumed."""
        return self.count >= self.maximum

    async def reserve(self) -> bool:
        async with self._lock:
            if self.count >= self.maximum:
                return False
            now = time.monotonic()
            while self._window and now - self._window[0] >= 60.0:
                self._window.popleft()
            if len(self._window) >= self.max_per_minute:
                return False
            self._window.append(now)
            self.count += 1
            return True


async def _search_scope_variant(
    client: httpx.AsyncClient,
    request: CodeSearchRequest,
    plan: QueryPlan,
    token: str,
    scope: str | None,
    variant: str,
    gate: _RequestGate,
) -> tuple[list[CodeSearchHit], list[Diagnostic]]:
    hits: list[CodeSearchHit] = []
    diagnostics: list[Diagnostic] = []
    per_page = min(request.budget.max_results_per_search, 100)
    collection_limit = min(
        1000, request.budget.max_results_per_search * (10 if request.deep else 1)
    )
    total_count = 0
    max_pages = 1
    for page in range(1, 11 if request.deep else 2):
        if page > max_pages:
            break
        if not await gate.reserve():
            diagnostics.append(
                _diagnostic(
                    (
                        "GitHub code-search request budget exhausted"
                        if gate.budget_exhausted
                        else "GitHub code-search rate limit reached (10 req/min); request skipped"
                    ),
                    outcome="partial",
                    failure_kind="budget" if gate.budget_exhausted else "rate_limit",
                    query=variant,
                    details={"max_code_search_requests": request.budget.max_code_search_requests},
                )
            )
            break
        code_query = _build_code_query(variant, scope=scope, request=request, plan=plan)
        response = await client.get(
            _GITHUB_CODE_SEARCH_URL,
            params={"q": code_query, "per_page": per_page, "page": page},
            headers=_headers(token, text_matches=True),
            timeout=settings.search_retrieve_budget_seconds,
        )
        if response.status_code in (403, 429):
            retry_wait = _retry_after(response) or 2.0
            await asyncio.sleep(min(retry_wait, 60.0))
            if not await gate.reserve():
                diagnostics.append(
                    _diagnostic(
                        (
                            "GitHub code-search request budget exhausted"
                            if gate.budget_exhausted
                            else "GitHub code-search rate limit reached (10 req/min); retry skipped"
                        ),
                        outcome="partial",
                        failure_kind="budget" if gate.budget_exhausted else "rate_limit",
                        query=variant,
                        details={
                            "max_code_search_requests": request.budget.max_code_search_requests
                        },
                    )
                )
                break
            response = await client.get(
                _GITHUB_CODE_SEARCH_URL,
                params={"q": code_query, "per_page": per_page, "page": page},
                headers=_headers(token, text_matches=True),
                timeout=settings.search_retrieve_budget_seconds,
            )
        if response.status_code != 200:
            diagnostics.append(
                _diagnostic(
                    f"GitHub code search returned HTTP {response.status_code}",
                    query=code_query,
                    response=response,
                )
            )
            break
        payload = _json(response)
        if payload is None:
            diagnostics.append(
                _diagnostic("GitHub code search returned invalid JSON", query=code_query)
            )
            break
        page_hits, total_count, incomplete = _parse_code_items(
            payload,
            provider="github",
            query_variant=variant,
            page=page,
            per_page=per_page,
            max_results=collection_limit,
        )
        if scope is None and plan.mode == "discovery":
            page_hits = [hit for hit in page_hits if not _is_low_value_global_discovery_hit(hit)]
        for hit in page_hits:
            hit.source_metadata["repository_scoped"] = scope is not None
        hits.extend(page_hits)
        if incomplete:
            diagnostics.append(
                _diagnostic(
                    "GitHub reported incomplete code-search results",
                    outcome="partial",
                    failure_kind="incomplete_index",
                    query=code_query,
                    details={"incomplete_results": True},
                )
            )
        if not request.deep or not page_hits:
            break
        max_pages = min(10, max(1, (min(total_count, 1000) + per_page - 1) // per_page))
        if page >= max_pages or len(hits) >= collection_limit:
            break
    return hits[:collection_limit], diagnostics


async def _probe_repo_state(
    client: httpx.AsyncClient,
    token: str,
    repository: str,
    query: str,
    gate: _RequestGate,
) -> Diagnostic | None:
    if not await gate.reserve():
        return _diagnostic(
            (
                "GitHub repository-state probe skipped because the request budget is exhausted"
                if gate.budget_exhausted
                else "GitHub repository-state probe skipped because the rate limit was reached"
            ),
            outcome="partial",
            failure_kind="budget" if gate.budget_exhausted else "rate_limit",
            query=query,
        )
    response = await client.get(
        f"{_GITHUB_API_URL}/repos/{repository}",
        headers=_headers(token),
        timeout=settings.search_retrieve_budget_seconds,
    )
    if response.status_code == 404:
        return _diagnostic(
            f"Repository scope not found: {repository}",
            outcome="partial",
            failure_kind="not_found",
            query=query,
        )
    if response.status_code != 200:
        return _diagnostic(
            f"Repository-state probe returned HTTP {response.status_code}",
            outcome="partial",
            query=query,
            response=response,
        )
    payload = _json(response) or {}
    full_name = payload.get("full_name")
    if isinstance(full_name, str) and full_name.casefold() != repository.casefold():
        return _diagnostic(
            f"Repository scope was renamed to {full_name}",
            outcome="partial",
            failure_kind="not_found",
            query=query,
            details={"renamed_to": full_name},
        )
    if payload.get("archived"):
        return _diagnostic(
            f"Repository scope is archived: {repository}",
            outcome="partial",
            failure_kind="not_found",
            query=query,
            details={"archived": True},
        )
    return None


async def search_github(
    plan: QueryPlan,
    request: CodeSearchRequest,
    *,
    http_client: httpx.AsyncClient,
) -> ProviderResponse:
    """Search GitHub code with bounded discovery, pagination, and diagnostics."""

    token = _token()
    if not token:
        return ProviderResponse(
            provider="github",
            diagnostics=[
                _diagnostic(
                    "GITHUB_TOKEN or GH_TOKEN is required for GitHub code search",
                    outcome="partial",
                    failure_kind="auth",
                    query=request.query,
                )
            ],
        )

    gate = _RequestGate(request.budget.max_code_search_requests, max_per_minute=8)
    diagnostics: list[Diagnostic] = []
    discovery_request_count = 0
    scopes, scope_diagnostics = _scope_repositories(request, plan)
    diagnostics.extend(scope_diagnostics)
    repositories: list[RepoCandidate] = []
    if scopes:
        repositories = [
            RepoCandidate(name_with_owner=scope, url=f"https://github.com/{scope}")
            for scope in scopes
        ]
    elif plan.mode == "discovery":
        repositories, discovery_diagnostics, discovery_requests = await _discover_repositories(
            http_client, plan, request, token
        )
        # Discovery GraphQL requests are accounted separately: they do not
        # consume code-search gate slots (the gate guards /search/code only).
        discovery_request_count = discovery_requests
        diagnostics.extend(discovery_diagnostics)

    variants = tuple(dict.fromkeys(plan.variants[: request.budget.max_query_variants])) or (
        plan.api_query,
    )
    work: list[tuple[str | None, str]] = []
    if scopes:
        for variant in variants:
            for scope in scopes[: request.budget.max_repositories]:
                work.append((scope, variant))
    elif repositories:
        proof_variants = _repository_proof_variants(plan, variants)[:2]
        candidate_limit = 3 if len(proof_variants) > 1 else 6
        work.extend(
            (repo.name_with_owner, variant)
            for repo in repositories[:candidate_limit]
            for variant in proof_variants
        )
        work.extend((None, variant) for variant in proof_variants)
    else:
        work.extend((None, variant) for variant in variants)
    work = work[: request.budget.max_code_search_requests]
    compiled_queries = list(
        dict.fromkeys(
            _build_code_query(variant, scope=scope, request=request, plan=plan)
            for scope, variant in work
        )
    )

    sem = asyncio.Semaphore(2)

    async def _throttled_search(scope_val: str | None, var_val: str) -> tuple[list[CodeSearchHit], list[Diagnostic]]:
        async with sem:
            await asyncio.sleep(0.05)
            return await _search_scope_variant(http_client, request, plan, token, scope_val, var_val, gate)

    results = await asyncio.gather(
        *(_throttled_search(scope, variant) for scope, variant in work),
        return_exceptions=True,
    )
    hits: list[CodeSearchHit] = []
    scoped_nonempty: set[str] = set()
    for item in results:
        if isinstance(item, BaseException):
            LOGGER.warning("GitHub code-search branch failed: %s", item)
            diagnostics.append(
                _diagnostic(f"GitHub code-search branch failed: {type(item).__name__}")
            )
            continue
        branch_hits, branch_diagnostics = item
        hits.extend(branch_hits)
        diagnostics.extend(branch_diagnostics)
        scoped_nonempty.update(hit.repository for hit in branch_hits if hit.repository)

    if scopes:
        for scope in scopes:
            if scope not in scoped_nonempty:
                state = await _probe_repo_state(http_client, token, scope, request.query, gate)
                if state is not None:
                    diagnostics.append(state)
    repo_by_name = {repo.name_with_owner: repo for repo in repositories}
    for hit in hits:
        repo = repo_by_name.get(hit.repository or "")
        if repo:
            hit.source_metadata.update(
                {
                    "stars": repo.stars,
                    "forks": repo.forks,
                    "pushed_at": repo.pushed_at,
                    "language": repo.language,
                    "archived": repo.archived,
                }
            )
            hit.published_date = repo.pushed_at
    return ProviderResponse(
        provider="github",
        hits=hits,
        diagnostics=diagnostics,
        request_count=gate.count + discovery_request_count,
        metadata={
            "repositories": [repo.model_dump(exclude_none=True) for repo in repositories],
            "compiled_queries": compiled_queries,
            "repository_queries": (
                list(_build_repository_queries(plan)) if repositories and not scopes else []
            ),
            "engine": "github_rest_legacy_code_search",
        },
    )


_HYDRATE_QUERY_TEMPLATE = """query HydrateFiles({variables}) {{
{fields}
}}"""


def _hydrate_query(group: list[CodeSearchHit]) -> tuple[str, dict[str, str]]:
    variables: dict[str, str] = {}
    fields: list[str] = []
    for index, hit in enumerate(group):
        repository = hit.repository or ""
        owner, _, repo = repository.partition("/")
        commit = hit.commit_oid or "HEAD"
        path = hit.path or ""
        variables[f"owner{index}"] = owner
        variables[f"repo{index}"] = repo
        variables[f"expr{index}"] = f"{commit}:{path}"
        fields.append(
            "  f{0}: repository(owner: $owner{0}, name: $repo{0}) {{\n"
            "    object(expression: $expr{0}) {{ oid ... on Blob {{ byteSize isBinary text }} }}\n"
            "  }}".format(index)
        )
    declarations = " ".join(f"${name}: String!" for name in variables)
    return _HYDRATE_QUERY_TEMPLATE.format(
        variables=declarations, fields="\n".join(fields)
    ), variables


async def hydrate_github_hits(
    hits: list[CodeSearchHit],
    *,
    http_client: httpx.AsyncClient,
    token: str | None = None,
    max_files: int = 25,
    max_chars_per_file: int = 200_000,
    deep: bool = False,
) -> tuple[list[Diagnostic], int, bool]:
    """Hydrate selected GitHub hits with commit-pinned GraphQL aliases."""

    token = token or _token()
    if not token:
        return (
            [
                _diagnostic(
                    "GitHub hydration skipped because GITHUB_TOKEN or GH_TOKEN is missing",
                    outcome="partial",
                    failure_kind="auth",
                )
            ],
            0,
            False,
        )
    safe_max_chars_per_file = max(1, max_chars_per_file)
    selected: list[CodeSearchHit] = []
    seen: set[tuple[str, str, str]] = set()
    for hit in hits:
        if not hit.repository or not hit.path:
            continue
        key = (hit.repository, hit.path, hit.commit_oid or "HEAD")
        if key in seen:
            continue
        seen.add(key)
        selected.append(hit)
        if len(selected) >= max_files:
            break
    groups: dict[str, list[CodeSearchHit]] = defaultdict(list)
    for hit in selected:
        groups[hit.repository or ""].append(hit)
    diagnostics: list[Diagnostic] = []
    hydrated_count = 0
    truncated = False
    for repository, group in groups.items():
        query, variables = _hydrate_query(group)
        response = await http_client.post(
            _GITHUB_GRAPHQL_URL,
            headers=_headers(token),
            json={"query": query, "variables": variables},
            timeout=settings.search_retrieve_budget_seconds,
        )
        if response.status_code != 200:
            diagnostics.append(
                _diagnostic(
                    f"GitHub hydration returned HTTP {response.status_code}",
                    outcome="partial",
                    query=repository,
                    response=response,
                )
            )
            continue
        payload = _json(response)
        if payload is None:
            diagnostics.append(
                _diagnostic(
                    "GitHub hydration returned invalid JSON", outcome="partial", query=repository
                )
            )
            continue
        if payload.get("errors"):
            diagnostics.append(
                _diagnostic(_graphql_error_message(payload), outcome="partial", query=repository)
            )
        data = payload.get("data") or {}
        for index, hit in enumerate(group):
            node = data.get(f"f{index}") if isinstance(data, dict) else None
            blob = node.get("object") if isinstance(node, dict) else None
            if not isinstance(blob, dict):
                diagnostics.append(
                    _diagnostic(
                        f"GitHub could not hydrate {hit.repository}:{hit.path}",
                        outcome="partial",
                        failure_kind="not_found",
                    )
                )
                continue
            if blob.get("isBinary"):
                diagnostics.append(
                    _diagnostic(
                        f"Skipped binary GitHub file {hit.repository}:{hit.path}",
                        outcome="partial",
                        failure_kind="provider",
                        details={"binary": True},
                    )
                )
                continue
            text = blob.get("text")
            if not isinstance(text, str):
                diagnostics.append(
                    _diagnostic(
                        f"GitHub file has no textual content: {hit.repository}:{hit.path}",
                        outcome="partial",
                        failure_kind="provider",
                    )
                )
                continue
            blob_oid = blob.get("oid") if isinstance(blob.get("oid"), str) else None
            if hit.sha and blob_oid and hit.sha != blob_oid:
                diagnostics.append(
                    _diagnostic(
                        f"GitHub blob identity mismatch for {hit.repository}:{hit.path}",
                        outcome="partial",
                        failure_kind="provider",
                        details={"expected_blob_sha": hit.sha, "actual_blob_sha": blob_oid},
                    )
                )
                continue
            if blob_oid:
                hit.source_metadata["hydrated_blob_oid"] = blob_oid
            normalized_text = text.replace("\r\n", "\n")
            candidates = [fragment.text for fragment in hit.fragments if fragment.text]
            if hit.query_variant:
                candidates.extend(
                    item.strip('"') for item in re.findall(r'"[^"\n]+"|\S+', hit.query_variant)
                )
            location = -1
            for candidate in candidates:
                for cand_line in candidate.splitlines():
                    cleaned_line = cand_line.strip()
                    if len(cleaned_line) >= 4:
                        pos = normalized_text.find(cleaned_line)
                        if pos >= 0:
                            location = pos
                            break
                if location >= 0:
                    break
            if location < 0:
                for candidate in candidates:
                    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", candidate)
                    for word in words:
                        pos = normalized_text.find(word)
                        if pos >= 0:
                            location = pos
                            break
                    if location >= 0:
                        break
            lines = normalized_text.splitlines()
            full_source_chars = len(normalized_text)
            if full_source_chars <= safe_max_chars_per_file:
                hit.hydrated_source = normalized_text
                hit.hydrated_source_truncated = False
                hit.source_metadata.update(
                    {
                        "source_window_start": 1,
                        "source_window_end": len(lines),
                        "full_source_chars": full_source_chars,
                    }
                )
            else:
                truncated = True
                hit.hydrated_source_truncated = True
                if location >= 0:
                    match_line = normalized_text[:location].count("\n") + 1
                    hit.line_start = hit.line_start or match_line
                    hit.line_end = hit.line_end or match_line
                    before_lines = 160 if deep else 80
                    after_lines = 320 if deep else 160
                    window_start = max(0, match_line - 1 - before_lines)
                    window_end = min(len(lines), match_line + after_lines)
                    hit.hydrated_source = "\n".join(lines[window_start:window_end])
                    hit.source_metadata.update(
                        {
                            "source_window_start": window_start + 1,
                            "source_window_end": window_end,
                            "full_source_chars": full_source_chars,
                        }
                    )
                else:
                    fragment_location = -1
                    for fragment in hit.fragments:
                        if not fragment.text:
                            continue
                        fragment_text = fragment.text[:50]
                        position = normalized_text.find(fragment_text)
                        if position >= 0:
                            fragment_location = position
                            break
                    if fragment_location >= 0:
                        match_line = normalized_text[:fragment_location].count("\n") + 1
                        hit.line_start = hit.line_start or match_line
                        hit.line_end = hit.line_end or match_line
                        window_start = max(0, match_line - 1 - 80)
                        window_end = min(len(lines), match_line + 160)
                        hit.hydrated_source = "\n".join(lines[window_start:window_end])
                        hit.source_metadata.update(
                            {
                                "source_window_start": window_start + 1,
                                "source_window_end": window_end,
                                "full_source_chars": full_source_chars,
                            }
                        )
                    else:
                        hit.hydrated_source = "\n".join(lines[:800])
                        hit.source_metadata.update(
                            {
                                "source_window_start": 1,
                                "source_window_end": min(len(lines), 800),
                                "full_source_chars": full_source_chars,
                            }
                        )
            ast_classification = classify_source(
                normalized_text,
                language=language_for_path(hit.path),
                path=hit.path,
                source_line_start=1,
                match_line_start=hit.line_start,
                match_line_end=hit.line_end,
            )
            hit.source_metadata["ast_classification"] = ast_classification.as_metadata()
            hydrated_count += 1
    return diagnostics, hydrated_count, truncated


_QUERY_MAX_CHARS = 256
