"""GitHub issue thread producer returning RawDocument candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..github_api import graphql_paginate_comments, resolve_github_token, thread_values
from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverTarget,
    ThreadDocument,
    ThreadMessage,
)
from ..documents import build_thread_document


GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


class GitHubIssueError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubIssueTarget:
    owner: str
    repo: str
    number: int


_ISSUE_RE = re.compile(r"^/([^/]+)/([^/]+)/issues/(\d+)(?:/|$)")


def parse_github_issue_url(url: str) -> GitHubIssueTarget:
    """Parse a GitHub issue URL."""

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"github.com", "www.github.com"}:
        raise GitHubIssueError(f"Unsupported GitHub host: {host or '(missing)'}")
    path = parsed.path or ""
    match = _ISSUE_RE.match(path)
    if not match:
        raise GitHubIssueError("URL is not a recognized GitHub issue URL.")
    owner, repo, num = match.group(1), match.group(2), match.group(3)
    try:
        number = int(num)
    except Exception as exc:
        raise GitHubIssueError("Invalid issue number.") from exc
    return GitHubIssueTarget(owner=owner, repo=repo, number=number)


def match_github_issue(parsed: ParsedURL) -> ResolverTarget | None:
    """Return a :class:`ResolverTarget` for any GitHub issue URL; else ``None``."""

    try:
        target = parse_github_issue_url(parsed.url)
    except GitHubIssueError:
        return None
    return ResolverTarget(
        url=parsed.url,
        kind="github_issue",
        values={"owner": target.owner, "repo": target.repo, "number": str(target.number)},
    )


def _reaction_count(groups: Any, content: str) -> int:
    if not isinstance(groups, list):
        return 0
    for group in groups:
        if isinstance(group, dict) and group.get("content") == content:
            reactors = group.get("reactors") if isinstance(group.get("reactors"), dict) else {}
            count = reactors.get("totalCount") if isinstance(reactors, dict) else 0
            if count is None:
                return 0
            try:
                return int(count)
            except Exception:
                return 0
    return 0


# ---------------------------------------------------------------------------
# New path — RawDocument via shared client + threads renderer.
# ---------------------------------------------------------------------------


_RAW_ISSUE_QUERY = """
query ($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      id
      title
      url
      state
      createdAt
      body
      author { login }
      reactionGroups { content reactors { totalCount } }
      comments(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        totalCount
        nodes {
          id
          author { login }
          body
          createdAt
          url
          reactionGroups { content reactors { totalCount } }
        }
      }
    }
  }
}
"""


def _github_thread_messages(
    issue: dict[str, Any], comments: list[dict[str, Any]]
) -> tuple[ThreadMessage, ...]:
    out: list[ThreadMessage] = []
    issue_id = str(issue.get("id") or "issue_root")
    out.append(
        ThreadMessage(
            id=issue_id,
            role="question",
            body=str(issue.get("body") or ""),
            body_format="markdown",
            author=(
                str(issue.get("author", {}).get("login"))
                if isinstance(issue.get("author"), dict) and issue.get("author", {}).get("login")
                else None
            ),
            created_at=str(issue.get("createdAt") or "") or None,
            score=_reaction_count(issue.get("reactionGroups"), "THUMBS_UP") or None,
            accepted=False,
            permalink=str(issue.get("url") or "") or None,
            parent_id=None,
        )
    )
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        cid = str(comment.get("id") or "")
        if not cid:
            continue
        body = str(comment.get("body") or "")
        if not body.strip():
            continue
        author = None
        if isinstance(comment.get("author"), dict):
            author = str(comment["author"].get("login") or "") or None
        out.append(
            ThreadMessage(
                id=cid,
                role="answer",
                body=body,
                body_format="markdown",
                author=author,
                created_at=str(comment.get("createdAt") or "") or None,
                score=_reaction_count(comment.get("reactionGroups"), "THUMBS_UP") or None,
                accepted=False,
                permalink=str(comment.get("url") or "") or None,
                parent_id=issue_id,
            )
        )
    return tuple(out)


async def fetch_github_issue_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire a GitHub issue thread and return a :class:`RawDocument`."""

    owner, repo, number = thread_values(target)
    token = resolve_github_token()
    if not token:
        raise AcquisitionError(
            code="unauthorized",
            message="GITHUB_TOKEN not configured for GitHub issue fetch",
            status="blocked",
            retryable=False,
        )

    issue_payload, comments, total = await graphql_paginate_comments(
        ctx,
        query=_RAW_ISSUE_QUERY,
        token=token,
        variables={"owner": owner, "name": repo, "number": number},
        resource="issue",
        resource_label="issue",
    )

    messages = _github_thread_messages(issue_payload, comments)
    title = str(issue_payload.get("title") or f"Issue {number}").strip()
    thread: ThreadDocument = build_thread_document(
        title=title,
        url=target.url,
        messages=messages,
        metadata={
            "kind": "github_issue",
            "owner": owner,
            "repo": repo,
            "number": number,
            "state": str(issue_payload.get("state") or "") or None,
            "total_comments": total,
            "truncated": len(comments) < total,
        },
    )
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="github_issue_fetched",
            message=f"GitHub {owner}/{repo}#{number} ({len(messages)} messages)",
        ),
    )
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="github_issue",
        fetch_backend="github_graphql",
        body=thread,
        title=title,
        metadata={
            "owner": owner,
            "repo": repo,
            "number": number,
            "state": thread.metadata.get("state"),
            "comment_count": total,
            "truncated": thread.metadata.get("truncated"),
        },
        diagnostics=diagnostics,
        complete=bool(messages),
        scope="full",
    )


__all__ = [
    "GitHubIssueError",
    "GitHubIssueTarget",
    "parse_github_issue_url",
    "match_github_issue",
    "fetch_github_issue_raw",
]
